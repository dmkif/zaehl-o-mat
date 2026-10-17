import asyncio
import logging
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.auth import (
    bearer_scheme,
    get_or_create_user_from_oidc,
    get_current_user,
    issue_user_token,
    revoke_sessions,
)
from app.config import settings
from app.database import get_db
from app.limiter import limiter, _key_func as client_ip
from app.models import LoginCode, User, UserRole, OidcPkceState
from app.security_log import log_event
from app.schemas.auth import ExchangeRequest, TokenResponse, SuperadminLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# OIDC discovery config cache with 1 h TTL (D3)
_oidc_config_cache: Optional[dict] = None
_oidc_config_fetched_at: Optional[datetime] = None
_OIDC_CACHE_TTL = timedelta(hours=1)
# F-026: after a failed discovery fetch, fail fast for a short while instead of
# letting every anonymous request wait for the IdP timeout again.
_oidc_failed_at: Optional[datetime] = None
_OIDC_NEGATIVE_TTL = timedelta(seconds=30)
_oidc_lock = asyncio.Lock()

BINDER_COOKIE = "oidc_binder"
_STATE_TTL = timedelta(minutes=10)


def _idp_timeout() -> httpx.Timeout:
    """Bounded IdP calls: short connect, overall read bound from the setting."""
    total = settings.oidc_http_timeout_s
    return httpx.Timeout(total, connect=min(3.0, total))


def _idp_error(exc: Exception) -> HTTPException:
    """Map an httpx failure to a clean gateway error (never leaks details)."""
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Identity provider timed out")
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Identity provider unavailable")


def _discovery_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Identity provider temporarily unavailable",
        headers={"Retry-After": str(int(_OIDC_NEGATIVE_TTL.total_seconds()))},
    )


async def _oidc_config() -> dict:
    global _oidc_config_cache, _oidc_config_fetched_at, _oidc_failed_at
    now = datetime.now(timezone.utc)
    if (
        _oidc_config_cache
        and _oidc_config_fetched_at
        and now - _oidc_config_fetched_at < _OIDC_CACHE_TTL
    ):
        return _oidc_config_cache
    if _oidc_failed_at and now - _oidc_failed_at < _OIDC_NEGATIVE_TTL:
        if _oidc_config_cache:
            return _oidc_config_cache  # stale beats unavailable
        raise _discovery_unavailable()
    async with _oidc_lock:
        # Double-checked locking: another coroutine may have refreshed while we waited
        now = datetime.now(timezone.utc)
        if (
            _oidc_config_cache
            and _oidc_config_fetched_at
            and now - _oidc_config_fetched_at < _OIDC_CACHE_TTL
        ):
            return _oidc_config_cache
        if _oidc_failed_at and now - _oidc_failed_at < _OIDC_NEGATIVE_TTL:
            if _oidc_config_cache:
                return _oidc_config_cache
            raise _discovery_unavailable()
        try:
            async with httpx.AsyncClient(timeout=_idp_timeout()) as client:
                resp = await client.get(settings.oidc_discovery_url)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            _oidc_failed_at = datetime.now(timezone.utc)
            logger.warning("OIDC discovery failed: %s", type(exc).__name__)
            if _oidc_config_cache:
                return _oidc_config_cache
            raise _idp_error(exc)
        _oidc_config_cache = data
        _oidc_config_fetched_at = now
        _oidc_failed_at = None
    return _oidc_config_cache


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _cleanup_expired_pkce(db: Session) -> None:
    """Delete PKCE state rows older than 10 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.query(OidcPkceState).filter(OidcPkceState.created_at < cutoff).delete()
    db.commit()


def _hash_binder(binder: str) -> str:
    return hashlib.sha256(binder.encode()).hexdigest()


def _nonce_for(state: str) -> str:
    """Per-login OIDC nonce, derived from the state with the server secret (no storage)."""
    return hmac.new(settings.jwt_secret_key.encode(), b"oidc-nonce:" + state.encode(), hashlib.sha256).hexdigest()[:32]


def _rate() -> str:
    return settings.oidc_rate_limit


def _prepare_login(db: Session, state: str, verifier: str, binder: str) -> None:
    """Purge expired states, enforce the pending cap, store the new state (sync, DB)."""
    _cleanup_expired_pkce(db)
    if db.query(OidcPkceState).count() >= settings.oidc_max_pending_states:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many pending sign-ins, try again",
            headers={"Retry-After": "30"},
        )
    # The browser-binding hash is kept next to the verifier ("<verifier>.<sha256(binder)>").
    db.add(OidcPkceState(state=state, code_verifier=f"{verifier}.{_hash_binder(binder)}"))
    db.commit()


@router.get("/login")
@limiter.limit(_rate)
async def login(request: Request, db: Session = Depends(get_db)):
    """Redirect browser to Authentik for OIDC authentication."""
    cfg = await _oidc_config()  # fails fast (no DB write) while the IdP is down
    state = secrets.token_urlsafe(16)
    binder = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    try:
        await run_in_threadpool(_prepare_login, db, state, verifier, binder)
    except PoolTimeoutError:
        raise _pool_busy()

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile groups",
        "state": state,
        "nonce": _nonce_for(state),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = cfg["authorization_endpoint"] + "?" + urlencode(params)
    resp = RedirectResponse(url=url)
    # F-017: bind the login to this browser (callback requires the cookie).
    resp.set_cookie(
        BINDER_COOKIE, binder, max_age=int(_STATE_TTL.total_seconds()), path="/api/auth",
        httponly=True, samesite="lax", secure=settings.app_base_url.lower().startswith("https://"),
    )
    return resp


def _pool_busy() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Service busy, try again",
        headers={"Retry-After": "5"},
    )


def _consume_pkce_state(db: Session, state: str, binder: Optional[str] = None) -> str:
    """Validate and delete the PKCE row, return its code_verifier (sync, DB).

    The row is consumed first (single use, even on a binder mismatch); then the
    browser binding (F-017) is checked.
    """
    # A NUL byte makes PostgreSQL raise (500); real states are ~43 URL-safe characters.
    if "\x00" in state or len(state) > 256:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")
    pkce_row = db.query(OidcPkceState).filter(OidcPkceState.state == state).first()
    if not pkce_row:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")

    stored = pkce_row.code_verifier
    created_at = pkce_row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    db.delete(pkce_row)
    db.commit()

    # Validate TTL (belt-and-suspenders alongside periodic cleanup)
    if datetime.now(timezone.utc) - created_at > _STATE_TTL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="State expired")

    verifier, _, binder_hash = stored.partition(".")
    if not binder or not binder_hash or not hmac.compare_digest(_hash_binder(binder), binder_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")
    return verifier


def _id_token_nonce(id_token: str) -> Optional[str]:
    """`nonce` claim of an id_token received over the direct TLS back channel
    (signature validation is not required there, OIDC Core 3.1.3.7); None if absent/unparsable."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        nonce = claims.get("nonce")
        return nonce if isinstance(nonce, str) else None
    except Exception:
        return None


_LOGIN_CODE_TTL = timedelta(seconds=60)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _sign_in(db: Session, userinfo: dict, ip: Optional[str] = None) -> str:
    """Create/update the user and return a single-use login code (sync, DB).

    The code (not the JWT, F-008) goes to the SPA; only its hash is stored.
    """
    user = get_or_create_user_from_oidc(db, userinfo)
    now = datetime.now(timezone.utc)
    db.query(LoginCode).filter(LoginCode.expires_at < now).delete()
    code = secrets.token_urlsafe(32)
    db.add(LoginCode(code_hash=_hash_code(code), user_id=user.id, expires_at=now + _LOGIN_CODE_TTL))
    db.commit()
    log_event("login", outcome="ok", actor=user.id, ip=ip)
    return code


@router.get("/callback")
@limiter.limit(_rate)
async def callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    """Exchange authorization code for tokens, return JWT."""
    ip = client_ip(request)
    binder = request.cookies.get(BINDER_COOKIE)
    try:
        verifier = await run_in_threadpool(_consume_pkce_state, db, state, binder)
    except HTTPException as exc:
        reason = "state_expired" if exc.detail == "State expired" else "invalid_state"
        log_event("login", outcome="failed", ip=ip, reason=reason)
        raise
    except PoolTimeoutError:
        raise _pool_busy()

    cfg = await _oidc_config()
    try:
        async with httpx.AsyncClient(timeout=_idp_timeout()) as client:
            token_resp = await client.post(
                cfg["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "code_verifier": verifier,
                },
            )
            if token_resp.status_code >= 500:
                log_event("login", outcome="failed", ip=ip, reason="idp_error")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Identity provider unavailable")
            if token_resp.status_code != 200:
                log_event("login", outcome="failed", ip=ip, reason="token_exchange_failed")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token exchange failed")

            tokens = token_resp.json()
            id_token = tokens.get("id_token")
            if id_token is not None:
                # F-017: a processed id_token must carry the nonce of this login.
                got = _id_token_nonce(id_token) if isinstance(id_token, str) else None
                if got is None or not hmac.compare_digest(got, _nonce_for(state)):
                    log_event("login", outcome="failed", ip=ip, reason="nonce_mismatch")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")
            access_token_oidc = tokens.get("access_token")
            if not access_token_oidc:
                log_event("login", outcome="failed", ip=ip, reason="no_access_token")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC provider returned no access token")

            userinfo_resp = await client.get(
                cfg["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {access_token_oidc}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log_event("login", outcome="failed", ip=ip, reason="idp_unavailable")
        raise _idp_error(exc)

    # Sync DB work must not run on the event loop: with the pool exhausted it
    # would block every request incl. /health (B-32). Pool timeout -> 503.
    try:
        login_code = await run_in_threadpool(_sign_in, db, userinfo, ip)
    except PoolTimeoutError:
        raise _pool_busy()
    # F-008: no JWT in the URL. The SPA route /auth/token reads the single-use
    # code from the fragment (never sent to servers) and POSTs it to /auth/exchange.
    frontend_url = settings.app_base_url.rstrip("/")
    resp = RedirectResponse(url=f"{frontend_url}/auth/token#code={login_code}")
    resp.delete_cookie(BINDER_COOKIE, path="/api/auth")
    return resp


def _consume_login_code(db: Session, code: str) -> Optional[User]:
    """Atomically consume a login code; the user, or None for any failure."""
    h = _hash_code(code)
    row = db.query(LoginCode).filter(LoginCode.code_hash == h).first()
    if not row:
        return None
    user_id, expires_at = row.user_id, row.expires_at
    consumed = db.query(LoginCode).filter(LoginCode.code_hash == h).delete()
    db.commit()
    if consumed != 1:  # lost a race against a parallel exchange
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


@router.post("/exchange", response_model=TokenResponse)
@limiter.limit("10/minute")
def exchange(request: Request, body: ExchangeRequest, db: Session = Depends(get_db)):
    """Swap the single-use login code from the callback redirect for the session JWT."""
    ip = client_ip(request)
    user = _consume_login_code(db, body.code)
    if user is None:
        log_event("login_exchange", outcome="failed", ip=ip, reason="invalid_or_expired_code")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code")
    log_event("login_exchange", actor=user.id, ip=ip)
    return TokenResponse(access_token=issue_user_token(user), token_type="bearer")  # nosec B106 - OAuth2 spec literal


@router.post("/superadmin-login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def superadmin_login(
    request: Request,
    body: SuperadminLoginRequest,
    db: Session = Depends(get_db),
):
    """Local login for superadmin account (bypasses OIDC)."""
    ip = client_ip(request)

    def _fail(reason: str) -> HTTPException:
        log_event("superadmin_login", outcome="failed", ip=ip, reason=reason)
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not settings.superadmin_password:
        raise _fail("not_configured")
    # compare_digest raises TypeError on non-ASCII str; compare UTF-8 bytes.
    username_ok = secrets.compare_digest(body.username.encode(), settings.superadmin_user.encode())
    password_ok = secrets.compare_digest(body.password.encode(), settings.superadmin_password.encode())
    if not (username_ok and password_ok):
        raise _fail("bad_credentials")

    user = db.query(User).filter(User.username == settings.superadmin_user).first()
    if not user:
        user = User(
            username=settings.superadmin_user,
            email=f"{settings.superadmin_user}@localhost",
            role=UserRole.superadmin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_active:
        raise _fail("user_inactive")
    elif user.role != UserRole.superadmin:
        # F-006: the env-configured superadmin always carries the superadmin role
        # (a demoted/edited row must not silently persist), and the token must match.
        log_event("superadmin_role_restored", actor=user.id, ip=ip, reason=f"was_{user.role.value}")
        user.role = UserRole.superadmin
        db.commit()
        db.refresh(user)

    log_event("superadmin_login", actor=user.id, ip=ip)
    token = issue_user_token(user)
    return TokenResponse(access_token=token, token_type="bearer")  # nosec B106 - OAuth2 spec literal, not a credential


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role.value,
    }


@router.post("/logout")
def logout(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Revoke all of the caller's tokens. Always 200 so the SPA can finish logging out
    even with an expired/unknown token (then nothing is revoked)."""
    user = None
    if credentials:
        try:
            user = get_current_user(request, credentials, db)
        except HTTPException:
            user = None
    if user is not None:
        revoke_sessions(db, user, actor=user.id, ip=client_ip(request), reason="logout")
        log_event("logout", actor=user.id, ip=client_ip(request))
    return JSONResponse({"detail": "Logged out"})
