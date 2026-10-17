import asyncio
import hashlib
import os
import secrets
import base64
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

# OIDC discovery config cache with 1 h TTL (D3)
_oidc_config_cache: Optional[dict] = None
_oidc_config_fetched_at: Optional[datetime] = None
_OIDC_CACHE_TTL = timedelta(hours=1)
_oidc_lock = asyncio.Lock()


async def _oidc_config() -> dict:
    global _oidc_config_cache, _oidc_config_fetched_at
    now = datetime.now(timezone.utc)
    if (
        _oidc_config_cache
        and _oidc_config_fetched_at
        and now - _oidc_config_fetched_at < _OIDC_CACHE_TTL
    ):
        return _oidc_config_cache
    async with _oidc_lock:
        # Double-checked locking: another coroutine may have refreshed while we waited
        now = datetime.now(timezone.utc)
        if (
            _oidc_config_cache
            and _oidc_config_fetched_at
            and now - _oidc_config_fetched_at < _OIDC_CACHE_TTL
        ):
            return _oidc_config_cache
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(settings.oidc_discovery_url)
            resp.raise_for_status()
            _oidc_config_cache = resp.json()
            _oidc_config_fetched_at = now
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


@router.get("/login")
async def login(db: Session = Depends(get_db)):
    """Redirect browser to Authentik for OIDC authentication."""
    cfg = await _oidc_config()
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()

    _cleanup_expired_pkce(db)
    db.add(OidcPkceState(state=state, code_verifier=verifier))
    db.commit()

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile groups",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = cfg["authorization_endpoint"] + "?" + urlencode(params)
    return RedirectResponse(url=url)


def _pool_busy() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Service busy, try again",
        headers={"Retry-After": "5"},
    )


def _consume_pkce_state(db: Session, state: str) -> str:
    """Validate and delete the PKCE row, return its code_verifier (sync, DB)."""
    pkce_row = db.query(OidcPkceState).filter(OidcPkceState.state == state).first()
    if not pkce_row:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")

    # Validate TTL (belt-and-suspenders alongside periodic cleanup)
    age = datetime.now(timezone.utc) - pkce_row.created_at
    if age > timedelta(minutes=10):
        db.delete(pkce_row)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="State expired")

    verifier = pkce_row.code_verifier
    db.delete(pkce_row)
    db.commit()
    return verifier


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
async def callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    """Exchange authorization code for tokens, return JWT."""
    ip = client_ip(request)
    try:
        verifier = await run_in_threadpool(_consume_pkce_state, db, state)
    except HTTPException as exc:
        reason = "state_expired" if exc.detail == "State expired" else "invalid_state"
        log_event("login", outcome="failed", ip=ip, reason=reason)
        raise
    except PoolTimeoutError:
        raise _pool_busy()

    cfg = await _oidc_config()
    async with httpx.AsyncClient(timeout=15) as client:
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
        if token_resp.status_code != 200:
            log_event("login", outcome="failed", ip=ip, reason="token_exchange_failed")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token exchange failed")

        tokens = token_resp.json()
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

    # Sync DB work must not run on the event loop: with the pool exhausted it
    # would block every request incl. /health (B-32). Pool timeout -> 503.
    try:
        login_code = await run_in_threadpool(_sign_in, db, userinfo, ip)
    except PoolTimeoutError:
        raise _pool_busy()
    # F-008: no JWT in the URL. The SPA route /auth/token reads the single-use
    # code from the fragment (never sent to servers) and POSTs it to /auth/exchange.
    frontend_url = settings.app_base_url.rstrip("/")
    return RedirectResponse(url=f"{frontend_url}/auth/token#code={login_code}")


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
