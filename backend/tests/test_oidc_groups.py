"""
Regression tests for security review 004:
  B-01  group claim shape handling (exact match only, no substring)
  B-32  OIDC callback must not run sync DB work on the event loop
  B-16  non-ASCII superadmin credentials -> 401, not 500
  JWT hardening: non-string sub/alg, mandatory finite exp
"""
import threading
import time
from datetime import timezone
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from authlib.jose import JsonWebToken
from fastapi import HTTPException

from app.auth import get_or_create_user_from_oidc
from app.config import settings
from app.models import OidcPkceState, UserRole
from app.routers.auth import _consume_pkce_state
from tests.conftest import make_user


def _data(groups, sub="sub-g"):
    return {"sub": sub, "email": "g@example.com", "preferred_username": "g",
            settings.oidc_groups_claim: groups}


# ── B-01 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("groups", [
    "xadminx", "adminx", "xadmin", "not-admin", "admin-ops",
    "admin verwalter", "admin,user", "user admin",
    '["admin"]', '{"admin": true}',
    "a" * 100_000 + "admin" + "b" * 100_000,
    {"admin": True}, {"groups": ["admin"]},
    None, 0, 1, True, False, 1.5, 3.14,
    [1, 2], [None, {"admin": 1}], [["admin"]], [b"admin"],
])
def test_bad_group_shapes_rejected_with_403(db, groups):
    with pytest.raises(HTTPException) as exc:
        get_or_create_user_from_oidc(db, _data(groups))
    assert exc.value.status_code == 403


def test_missing_groups_claim_rejected(db):
    with pytest.raises(HTTPException) as exc:
        get_or_create_user_from_oidc(db, {"sub": "s", "email": "a@b.c"})
    assert exc.value.status_code == 403


def test_exact_string_group_allowed(db):
    u = get_or_create_user_from_oidc(db, _data(settings.oidc_admin_group))
    assert u.role == UserRole.admin


def test_exact_list_allowed(db):
    u = get_or_create_user_from_oidc(db, _data([settings.oidc_manager_group]))
    assert u.role == UserRole.manager


def test_tuple_allowed(db):
    u = get_or_create_user_from_oidc(db, _data((settings.oidc_user_group,)))
    assert u.role == UserRole.user


def test_list_with_non_string_items_ignores_them(db):
    u = get_or_create_user_from_oidc(db, _data([1, None, settings.oidc_user_group, {"a": 1}]))
    assert u.role == UserRole.user


def test_list_substring_items_not_matched(db):
    with pytest.raises(HTTPException) as exc:
        get_or_create_user_from_oidc(db, _data(["xadminx", "administrators"]))
    assert exc.value.status_code == 403


# ── B-32 ──────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def test_callback_db_work_not_on_event_loop(client, db):
    """The async callback must hand its sync DB work to a worker thread, else
    N parallel sign-ins block the loop inside QueuePool._do_get (B-32).
    A real pool exhaustion needs PostgreSQL: rig experiment E-A06-001 with
    RIG_RACE_SIGNINS=100."""
    import hashlib
    db.add(OidcPkceState(state="st", code_verifier="v." + hashlib.sha256(b"b").hexdigest()))
    db.commit()
    # SQLite returns naive datetimes; PostgreSQL (prod) returns aware ones.
    real_consume = _consume_pkce_state

    def consume(d, s, b=None):
        row = d.query(OidcPkceState).filter(OidcPkceState.state == s).first()
        if row is not None and row.created_at.tzinfo is None:
            row.created_at = row.created_at.replace(tzinfo=timezone.utc)
        return real_consume(d, s, "b")

    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            seen["loop"] = threading.get_ident()
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp(200, {"access_token": "t"})

        async def get(self, *a, **k):
            return _Resp(200, {"sub": "s1", "email": "a@b.c",
                               settings.oidc_groups_claim: [settings.oidc_user_group]})

    real = get_or_create_user_from_oidc

    def spy(d, data):
        seen["db_work"] = threading.get_ident()
        return real(d, data)

    cfg = {"token_endpoint": "x", "userinfo_endpoint": "y"}
    with patch("app.routers.auth._oidc_config", new=AsyncMock(return_value=cfg)), \
         patch("app.routers.auth.httpx.AsyncClient", FakeClient), \
         patch("app.routers.auth._consume_pkce_state", consume), \
         patch("app.routers.auth.get_or_create_user_from_oidc", spy):
        r = client.get("/api/auth/callback?code=c&state=st", follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    assert seen["db_work"] != seen["loop"]


def test_callback_pool_timeout_gives_503(client, db):
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    with patch("app.routers.auth._consume_pkce_state", side_effect=PoolTimeout("pool")):
        r = client.get("/api/auth/callback?code=c&state=st", follow_redirects=False)
    assert r.status_code == 503


# ── B-16 ──────────────────────────────────────────────────────────────────────

def test_superadmin_login_non_ascii_returns_401(client, monkeypatch):
    monkeypatch.setattr(settings, "superadmin_password", "correct-pw")
    for body in ({"username": "ädmin", "password": "pässword"},
                 {"username": settings.superadmin_user, "password": "pässwörd✓"}):
        assert client.post("/api/auth/superadmin-login", json=body).status_code == 401


def test_superadmin_login_non_ascii_password_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "superadmin_password", "pässwörd")
    ok = client.post("/api/auth/superadmin-login",
                     json={"username": settings.superadmin_user, "password": "pässwörd"})
    assert ok.status_code == 200


# ── JWT hardening ─────────────────────────────────────────────────────────────

def _tok(payload, header=None):
    j = JsonWebToken([settings.jwt_algorithm])
    h = header or {"alg": settings.jwt_algorithm}
    return j.encode(h, payload, settings.jwt_secret_key).decode()


@pytest.mark.parametrize("payload", [
    {"sub": 123}, {"sub": ["a"]}, {"sub": {"a": 1}}, {"sub": True}, {"sub": None},
    {"sub": "not-a-uuid"},
])
def test_non_string_or_bad_sub_returns_401(client, payload):
    payload = {**payload, "exp": int(time.time()) + 600}
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_tok(payload)}"})
    assert r.status_code == 401


def test_non_string_alg_returns_401(client):
    import base64, json
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    for alg in (1, ["HS256"], {"a": 1}, None):
        t = f"{b({'alg': alg})}.{b({'sub': str(uuid.uuid4()), 'exp': int(time.time()) + 600})}.AAAA"
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 401, alg


def test_missing_exp_rejected(client, db):
    u = make_user(db)
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_tok({'sub': str(u.id)})}"})
    assert r.status_code == 401


@pytest.mark.parametrize("exp", [float("inf"), float("nan"), "9999999999", None, True])
def test_bad_exp_rejected(client, db, exp):
    u = make_user(db)
    r = client.get("/api/auth/me",
                   headers={"Authorization": f"Bearer {_tok({'sub': str(u.id), 'exp': exp})}"})
    assert r.status_code == 401
