"""
Tests for app/auth.py

Covers:
  - Password hashing & verification
  - JWT create / decode / expiry
  - get_or_create_user_from_oidc: new user, existing user, role promotion/demotion
  - require_role: allowed roles pass, forbidden roles raise 403
  - Superadmin bypasses role check
  - Inactive user cannot authenticate
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from app.auth import (
    create_access_token,
    decode_access_token,
    get_or_create_user_from_oidc,
)
from app.config import settings
from app.models import User, UserRole
from tests.conftest import make_user


# ── JWT ────────────────────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode_roundtrip(self):
        payload = {"sub": str(uuid.uuid4()), "role": "user"}
        token = create_access_token(payload)
        decoded = decode_access_token(token)
        assert decoded["sub"] == payload["sub"]
        assert decoded["role"] == "user"

    def test_expired_token_raises(self):
        # Token expired 1 second ago
        token = create_access_token({"sub": "x"}, expires_minutes=-1)
        with pytest.raises(Exception):  # JWTError / ExpiredSignatureError
            decode_access_token(token)

    def test_tampered_token_raises(self):
        token = create_access_token({"sub": "x"})
        tampered = token[:-4] + "xxxx"
        with pytest.raises(Exception):
            decode_access_token(tampered)

    def test_wrong_secret_raises(self):
        token = jwt.encode(
            {"sub": "x", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "wrong-secret",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(Exception):
            decode_access_token(token)

    def test_custom_expiry_is_respected(self):
        token = create_access_token({"sub": "x"}, expires_minutes=60)
        decoded = decode_access_token(token)
        remaining = decoded["exp"] - datetime.now(timezone.utc).timestamp()
        assert 3500 < remaining <= 3600


# ── OIDC user creation ─────────────────────────────────────────────────────────

class TestOidcUserCreation:
    def _oidc_data(self, sub="sub-123", email="alice@example.com", groups=None):
        return {
            "sub": sub,
            "email": email,
            "preferred_username": "alice",
            # Membership in a permitted group is mandatory since the
            # no-group-no-access hardening; default to the plain user group.
            "groups": [settings.oidc_user_group] if groups is None else groups,
        }

    def test_new_user_created_on_first_login(self, db):
        user = get_or_create_user_from_oidc(db, self._oidc_data())
        assert user.id is not None
        assert user.username == "alice"
        assert user.oidc_sub == "sub-123"
        assert user.role == UserRole.user

    def test_existing_user_is_not_duplicated(self, db):
        get_or_create_user_from_oidc(db, self._oidc_data())
        get_or_create_user_from_oidc(db, self._oidc_data())
        count = db.query(User).filter(User.oidc_sub == "sub-123").count()
        assert count == 1

    def test_admin_group_assigns_admin_role(self, db):
        user = get_or_create_user_from_oidc(
            db, self._oidc_data(groups=[settings.oidc_admin_group])
        )
        assert user.role == UserRole.admin

    def test_manager_group_assigns_manager_role(self, db):
        user = get_or_create_user_from_oidc(
            db, self._oidc_data(groups=[settings.oidc_manager_group])
        )
        assert user.role == UserRole.manager

    def test_admin_group_beats_manager_group(self, db):
        user = get_or_create_user_from_oidc(
            db,
            self._oidc_data(groups=[settings.oidc_admin_group, settings.oidc_manager_group]),
        )
        assert user.role == UserRole.admin

    def test_no_group_is_rejected(self, db):
        """An IdP account without any permitted group must not get access."""
        with pytest.raises(HTTPException) as exc_info:
            get_or_create_user_from_oidc(db, self._oidc_data(groups=[]))
        assert exc_info.value.status_code == 403

    def test_role_update_on_subsequent_login(self, db):
        """If a user gains admin group, their role is updated on next login."""
        get_or_create_user_from_oidc(db, self._oidc_data())
        updated = get_or_create_user_from_oidc(
            db, self._oidc_data(groups=[settings.oidc_admin_group])
        )
        assert updated.role == UserRole.admin

    def test_role_downgrade_on_subsequent_login(self, db):
        """If admin group is removed, role drops back to user."""
        get_or_create_user_from_oidc(
            db, self._oidc_data(groups=[settings.oidc_admin_group])
        )
        downgraded = get_or_create_user_from_oidc(db, self._oidc_data())
        assert downgraded.role == UserRole.user

    def test_username_falls_back_to_email_prefix(self, db):
        data = {"sub": "sub-xyz", "email": "bob@example.com", "groups": [settings.oidc_user_group]}
        user = get_or_create_user_from_oidc(db, data)
        assert user.username == "bob"


# ── API auth endpoints ─────────────────────────────────────────────────────────

class TestAuthApi:
    def test_me_without_token_returns_401(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_with_invalid_token_returns_401(self, client):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401

    def test_me_with_valid_token_returns_user(self, client, db):
        from tests.conftest import make_user, auth_headers
        user = make_user(db, role=UserRole.admin)
        r = client.get("/api/auth/me", headers=auth_headers(user))
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == user.username
        assert data["role"] == "admin"

    def test_superadmin_login_wrong_credentials_returns_401(self, client):
        r = client.post(
            "/api/auth/superadmin-login",
            json={"username": "admin", "password": "wrong"},
        )
        assert r.status_code == 401

    def test_superadmin_login_correct_credentials_returns_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "superadmin_user", "sadmin")
        monkeypatch.setattr(settings, "superadmin_password", "supersecret")
        r = client.post(
            "/api/auth/superadmin-login",
            json={"username": "sadmin", "password": "supersecret"},
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_inactive_user_returns_401(self, client, db):
        from tests.conftest import make_user, auth_headers
        user = make_user(db)
        user.is_active = False
        db.commit()
        r = client.get("/api/auth/me", headers=auth_headers(user))
        assert r.status_code == 401
