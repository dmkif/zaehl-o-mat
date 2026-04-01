import hashlib
import os
import secrets
import base64
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_or_create_user_from_oidc,
    verify_password,
    hash_password,
    get_current_user,
)
from app.config import settings
from app.database import get_db
from app.models import User, UserRole
from app.schemas.auth import TokenResponse, SuperadminLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory PKCE store (code_verifier keyed by state).
# For production scale, swap with Redis; fine for single-replica pod.
_pkce_store: dict[str, str] = {}
_oidc_config_cache: Optional[dict] = None


async def _oidc_config() -> dict:
    global _oidc_config_cache
    if _oidc_config_cache:
        return _oidc_config_cache
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(settings.oidc_discovery_url)
        resp.raise_for_status()
        _oidc_config_cache = resp.json()
    return _oidc_config_cache


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@router.get("/login")
async def login():
    """Redirect browser to Authentik for OIDC authentication."""
    cfg = await _oidc_config()
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()
    _pkce_store[state] = verifier

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile groups",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    from urllib.parse import urlencode
    url = cfg["authorization_endpoint"] + "?" + urlencode(params)
    return RedirectResponse(url=url)


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    """Exchange authorization code for tokens, return JWT."""
    verifier = _pkce_store.pop(state, None)
    if not verifier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")

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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token exchange failed")

        tokens = token_resp.json()

        userinfo_resp = await client.get(
            cfg["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json()

    user = get_or_create_user_from_oidc(db, userinfo)
    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    # Redirect to frontend with token in query param (SPA picks it up and stores in memory)
    frontend_url = settings.oidc_redirect_uri.replace("/auth/callback", "")
    return RedirectResponse(url=f"{frontend_url}/#/auth/token?token={access_token}")


@router.post("/superadmin-login", response_model=TokenResponse)
async def superadmin_login(
    body: SuperadminLoginRequest,
    db: Session = Depends(get_db),
):
    """Local login for superadmin account (bypasses OIDC)."""
    if (
        body.username != settings.superadmin_user
        or not settings.superadmin_password
        or body.password != settings.superadmin_password
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

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

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role.value,
    }


@router.post("/logout")
async def logout():
    # JWT is stateless; client must discard the token.
    # Optionally redirect to OIDC end_session_endpoint here.
    return JSONResponse({"detail": "Logged out"})
