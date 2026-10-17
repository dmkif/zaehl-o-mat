import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from authlib.jose import JsonWebToken, JoseError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.limiter import _key_func as client_ip
from app.models import User, UserRole
from app.security_log import log_event
from app.services import oidc_audit

bearer_scheme = HTTPBearer(auto_error=False)
_jwt = JsonWebToken([settings.jwt_algorithm])


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_access_token_expire_minutes
    )
    payload["exp"] = int(expire.timestamp())
    payload.setdefault("iat", int(datetime.now(timezone.utc).timestamp()))
    header = {"alg": settings.jwt_algorithm}
    return _jwt.encode(header, payload, settings.jwt_secret_key).decode()


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JoseError for every invalid token.

    `exp` is mandatory and must be a finite number; malformed headers/claims
    (non-string alg, etc.) are mapped to JoseError instead of leaking
    TypeError/AttributeError as a 500.
    """
    try:
        claims = _jwt.decode(
            token, settings.jwt_secret_key, claims_options={"exp": {"essential": True}}
        )
        claims.validate()
        # Our tokens carry only `alg` (+ optionally typ/kid); extension headers such as `zip`, `jwk`, `x5u` have
        # no business in a session token (F-034 residual, security rig E-A04-001).
        if set(claims.header) - {"alg", "typ", "kid"}:
            raise JoseError("Unsupported token header")
        exp = claims.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
            raise JoseError("Invalid exp claim")
    except JoseError:
        raise
    except Exception as exc:  # malformed token shapes must not become a 500
        raise JoseError("Invalid token") from exc
    return dict(claims)


def issue_user_token(user: User) -> str:
    """Session JWT for ``user``; `tv` binds it to the current revocation counter."""
    return create_access_token({"sub": str(user.id), "role": user.role.value, "tv": user.token_version})


def revoke_sessions(db: Session, user: User, *, actor: Optional[object] = None,
                    ip: Optional[str] = None, reason: str) -> None:
    """Invalidate every token issued to ``user`` so far (atomic counter increment)."""
    db.query(User).filter(User.id == user.id).update(
        {User.token_version: User.token_version + 1}, synchronize_session=False
    )
    db.commit()
    db.refresh(user)
    log_event("session_revoked", actor=actor, target=user.id, ip=ip, reason=reason)


def _normalize_groups(raw) -> list[str]:
    """Return the group names from an OIDC groups claim.

    Only a list/tuple of strings is accepted (non-string items are dropped).
    A plain string is ONE group name, compared by exact equality later (never
    substring). Anything else (dict, None, int, bool, float, ...) means "no
    groups" and leads to the 403 below.
    """
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [g for g in raw if isinstance(g, str)]
    return []


def _valid_sub(sub) -> bool:
    return isinstance(sub, str) and bool(sub.strip()) and len(sub) <= 255


def _unique_username(db: Session, base: str, sub: str) -> str:
    """F-019: a free local username; on collision a suffix derived from ``sub``.

    Never reuses (and never merges with) an existing account, in particular not
    the break-glass superadmin.
    """
    base = (base or "user").strip()[:120] or "user"
    if not db.query(User.id).filter(User.username == base).first():
        return base
    digest = hashlib.sha256(sub.encode()).hexdigest()
    for n in (8, 16, 32, 64):
        cand = f"{base}-{digest[:n]}"
        if not db.query(User.id).filter(User.username == cand).first():
            return cand
    return f"{base}-{uuid.uuid4().hex}"


def get_or_create_user_from_oidc(db: Session, oidc_data: dict) -> User:
    sub = oidc_data.get("sub")
    groups = _normalize_groups(oidc_data.get(settings.oidc_groups_claim))

    # Map OIDC groups → app role (highest privilege wins). Membership is an
    # exact match against the normalized list (never a substring test).
    # Users in none of the configured groups are rejected — an account at the
    # IdP alone must not grant access to the app.
    if settings.oidc_admin_group in groups:
        role = UserRole.admin
    elif settings.oidc_manager_group in groups:
        role = UserRole.manager
    elif settings.oidc_user_group in groups:
        role = UserRole.user
    else:
        # Reason + sign-in name only; group names only with the diagnostic switch (spec 003).
        oidc_audit.log_rejected(oidc_data, groups)
        # F-007: a known user who lost every permitted group loses open sessions too.
        known = db.query(User).filter(User.oidc_sub == sub).first() if _valid_sub(sub) else None
        if known:
            revoke_sessions(db, known, reason="idp_group_removed")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized: user is not a member of any permitted group",
        )

    oidc_audit.log_accepted(oidc_data, role, groups)

    if not _valid_sub(sub):
        # F-018: never let an IdP without a usable subject reach `oidc_sub IS NULL`
        # rows (the break-glass superadmin).
        log_event("login", outcome="failed", reason="invalid_sub")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC provider returned no valid subject")

    raw_email = oidc_data.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) else ""
    if not email:
        email = f"{hashlib.sha256(sub.encode()).hexdigest()[:32]}@oidc.invalid"
    pref = oidc_data.get("preferred_username")
    username = pref if isinstance(pref, str) and pref.strip() else email.split("@")[0]

    def _email_taken(candidate: str, own_id=None) -> bool:
        q = db.query(User.id).filter(User.email == candidate)
        if own_id is not None:
            q = q.filter(User.id != own_id)
        return q.first() is not None

    user = db.query(User).filter(User.oidc_sub == sub).first()
    if user:
        return _update_existing(db, user, role, email, _email_taken)

    # First login — create user. Collisions never merge accounts: a taken
    # username gets a derived suffix, a taken e-mail is a clean 409 (F-019).
    if _email_taken(email):
        # The holder may be the same identity whose concurrent first login committed between the lookup above and
        # this check (security rig E-A06-001): that is a sign-in, not a conflict.
        winner = db.query(User).filter(User.oidc_sub == sub).first()
        if winner:
            return _update_existing(db, winner, role, email, _email_taken)
        log_event("login", outcome="failed", reason="email_conflict")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account conflict: e-mail already in use")
    for _attempt in range(3):
        user = User(
            id=uuid.uuid4(),
            username=_unique_username(db, username, sub),
            email=email,
            oidc_sub=sub,
            role=role,
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
            db.refresh(user)
            return user
        except IntegrityError:
            db.rollback()
            # Concurrent first login of the same identity: use the winner's row.
            existing = db.query(User).filter(User.oidc_sub == sub).first()
            if existing:
                return _update_existing(db, existing, role, email, _email_taken)
            if _email_taken(email):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Account conflict: e-mail already in use")
            # otherwise a username raced us: retry with the next free name
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account conflict")


def _update_existing(db: Session, user: User, role: UserRole, email: str, email_taken) -> User:
    role_changed = user.role != role
    user.role = role
    if email != user.email and not email_taken(email, user.id):
        user.email = email  # a taken address is kept out, never merged
    if role_changed:
        user.token_version = User.token_version + 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account conflict")
    db.refresh(user)
    if role_changed:
        log_event("session_revoked", target=user.id, reason="role_changed_at_login")
    return user


def _deny(request: Request, reason: str, actor: Optional[object] = None) -> HTTPException:
    log_event("auth_denied", outcome="denied", actor=actor, ip=client_ip(request), reason=reason)
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_DENY_DETAIL[reason])


_DENY_DETAIL = {
    "no_credentials": "Not authenticated",
    "invalid_token": "Invalid token",  # nosec B105 - response text, not a credential
    "user_inactive_or_unknown": "User not found",
}


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise _deny(request, "no_credentials")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id or not isinstance(user_id, str):
            raise _deny(request, "invalid_token")
        tv = payload.get("tv")
        if isinstance(tv, bool) or not isinstance(tv, int):
            raise _deny(request, "invalid_token")
    except JoseError:
        raise _deny(request, "invalid_token")

    try:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    except ValueError:
        raise _deny(request, "invalid_token")
    if not user or not user.is_active:
        raise _deny(request, "user_inactive_or_unknown", actor=user.id if user else None)
    if tv != user.token_version:
        raise _deny(request, "invalid_token", actor=user.id)
    return user


def require_role(*roles: UserRole):
    def checker(request: Request, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles and current_user.role != UserRole.superadmin:
            log_event("auth_denied", outcome="denied", actor=current_user.id,
                      ip=client_ip(request), reason="insufficient_role")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return checker


require_admin = require_role(UserRole.admin)
require_manager = require_role(UserRole.admin, UserRole.manager)
require_user = require_role(UserRole.admin, UserRole.manager, UserRole.user)
