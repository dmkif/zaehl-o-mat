import math
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from authlib.jose import JsonWebToken, JoseError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)
_jwt = JsonWebToken([settings.jwt_algorithm])


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_access_token_expire_minutes
    )
    payload["exp"] = int(expire.timestamp())
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
        exp = claims.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
            raise JoseError("Invalid exp claim")
    except JoseError:
        raise
    except Exception as exc:  # malformed token shapes must not become a 500
        raise JoseError("Invalid token") from exc
    return dict(claims)


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


def get_or_create_user_from_oidc(db: Session, oidc_data: dict) -> User:
    sub = oidc_data.get("sub")
    email = oidc_data.get("email", "")
    username = oidc_data.get("preferred_username") or email.split("@")[0]
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized: user is not a member of any permitted group",
        )

    user = db.query(User).filter(User.oidc_sub == sub).first()
    if user:
        user.role = role
        user.email = email
        db.commit()
        db.refresh(user)
        return user

    # First login — create user (guard against concurrent first-login race condition)
    user = User(
        id=uuid.uuid4(),
        username=username,
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
        user = db.query(User).filter(User.oidc_sub == sub).first()
        if not user:
            raise
        user.role = role
        user.email = email
        db.commit()
        db.refresh(user)
        return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if not user_id or not isinstance(user_id, str):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JoseError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(*roles: UserRole):
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles and current_user.role != UserRole.superadmin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return checker


require_admin = require_role(UserRole.admin)
require_manager = require_role(UserRole.admin, UserRole.manager)
require_user = require_role(UserRole.admin, UserRole.manager, UserRole.user)
