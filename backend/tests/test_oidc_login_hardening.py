"""Feature 010: OIDC login hardening (F-017 binder cookie + nonce, F-018 sub,
F-019 collisions, F-026 rate limit / pending cap / IdP errors)."""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

from app.auth import get_or_create_user_from_oidc
from app.config import settings
from app.models import OidcPkceState, User, UserRole
from app.routers import auth as ra
from tests.conftest import make_user

CFG = {
    "authorization_endpoint": "http://idp/authorize",
    "token_endpoint": "http://idp/t",
    "userinfo_endpoint": "http://idp/u",
}


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))


def _fake_client(token=None, userinfo=None, post_exc=None, get_exc=None):
    token = token if token is not None else _Resp(200, {"access_token": "t"})
    userinfo = userinfo if userinfo is not None else _Resp(200, {
        "sub": "sub-1", "email": "s@example.com", "preferred_username": "s",
        settings.oidc_groups_claim: [settings.oidc_user_group]})

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            if post_exc:
                raise post_exc
            return token
        async def get(self, *a, **k):
            if get_exc:
                raise get_exc
            return userinfo
    return FakeClient


def _login(client):
    with patch.object(ra, "_oidc_config", new=AsyncMock(return_value=CFG)):
        return client.get("/api/auth/login", follow_redirects=False)


def _state_of(resp):
    return parse_qs(urlparse(resp.headers["location"]).query)


def _binder(resp):
    return resp.cookies.get("oidc_binder")


def _callback(client, state, binder, fake=None):
    headers = {"cookie": f"oidc_binder={binder}"} if binder else {}
    with patch.object(ra, "_oidc_config", new=AsyncMock(return_value=CFG)), \
         patch.object(ra.httpx, "AsyncClient", fake or _fake_client()):
        return client.get(f"/api/auth/callback?code=c&state={state}", headers=headers, follow_redirects=False)


# ------------------------------------------------------------------ F-017

def test_login_sets_binder_cookie_and_nonce(client, db):
    r = _login(client)
    assert r.status_code in (302, 307)
    q = _state_of(r)
    assert q["nonce"] and q["state"]
    sc = r.headers["set-cookie"].lower()
    assert "oidc_binder=" in sc and "httponly" in sc and "samesite=lax" in sc
    assert "max-age=" in sc
    # only a hash is stored, never the cookie value itself
    row = db.query(OidcPkceState).one()
    assert _binder(r) not in row.code_verifier


def test_callback_with_right_binder_succeeds(client, db):
    r = _login(client)
    cb = _callback(client, _state_of(r)["state"][0], _binder(r))
    assert cb.status_code in (302, 307) and "#code=" in cb.headers["location"]


def test_callback_without_binder_rejected(client, db):
    r = _login(client)
    cb = _callback(client, _state_of(r)["state"][0], None)
    assert cb.status_code == 400


def test_callback_with_foreign_binder_rejected(client, db):
    a, b = _login(client), _login(client)
    cb = _callback(client, _state_of(a)["state"][0], _binder(b))
    assert cb.status_code == 400
    assert db.query(User).count() == 0


def test_state_is_single_use_even_after_binder_mismatch(client, db):
    r = _login(client)
    st = _state_of(r)["state"][0]
    assert _callback(client, st, "wrong").status_code == 400
    assert _callback(client, st, _binder(r)).status_code == 400


def test_id_token_nonce_mismatch_rejected(client, db):
    import base64, json
    def idt(nonce):
        body = base64.urlsafe_b64encode(json.dumps({"nonce": nonce}).encode()).rstrip(b"=").decode()
        return f"e30.{body}.sig"
    r = _login(client)
    fake = _fake_client(token=_Resp(200, {"access_token": "t", "id_token": idt("evil")}))
    assert _callback(client, _state_of(r)["state"][0], _binder(r), fake).status_code == 400
    r = _login(client)
    nonce = _state_of(r)["nonce"][0]
    fake = _fake_client(token=_Resp(200, {"access_token": "t", "id_token": idt(nonce)}))
    assert _callback(client, _state_of(r)["state"][0], _binder(r), fake).status_code in (302, 307)


# ------------------------------------------------------------------ F-018

@pytest.mark.parametrize("sub", [None, "", "   ", 123, "x" * 256, ["a"]])
def test_bad_sub_rejected_and_superadmin_untouched(db, sub):
    sa = User(username="root", email="root@localhost", role=UserRole.superadmin, is_active=True)
    db.add(sa); db.commit()
    data = {"email": "evil@example.com", "preferred_username": "evil", settings.oidc_groups_claim: [settings.oidc_admin_group]}
    if sub is not None:
        data["sub"] = sub
    with pytest.raises(HTTPException) as e:
        get_or_create_user_from_oidc(db, data)
    assert e.value.status_code in (400, 502)
    db.refresh(sa)
    assert sa.role == UserRole.superadmin and sa.email == "root@localhost"


def test_callback_without_sub_is_502_not_500(client, db):
    r = _login(client)
    fake = _fake_client(userinfo=_Resp(200, {"email": "e@example.com", settings.oidc_groups_claim: [settings.oidc_user_group]}))
    assert _callback(client, _state_of(r)["state"][0], _binder(r), fake).status_code == 502


# ------------------------------------------------------------------ F-019

def _data(sub, username="alice", email="a@example.com"):
    return {"sub": sub, "preferred_username": username, "email": email, settings.oidc_groups_claim: [settings.oidc_user_group]}


def test_same_username_different_identity_gets_unique_name(db):
    u1 = get_or_create_user_from_oidc(db, _data("s1", email="a1@example.com"))
    u2 = get_or_create_user_from_oidc(db, _data("s2", email="a2@example.com"))
    assert u1.id != u2.id and u1.username == "alice" and u2.username != "alice"
    assert u2.username.startswith("alice")


def test_username_like_superadmin_never_merges(db):
    sa = User(username="root", email="root@localhost", role=UserRole.superadmin, is_active=True)
    db.add(sa); db.commit()
    u = get_or_create_user_from_oidc(db, _data("s9", username="root", email="r@example.com"))
    assert u.id != sa.id and u.username != "root" and u.role == UserRole.user
    db.refresh(sa)
    assert sa.oidc_sub is None and sa.role == UserRole.superadmin


def test_email_collision_new_identity_is_409_no_merge(db):
    make_user(db, username="other", email="a@example.com")
    with pytest.raises(HTTPException) as e:
        get_or_create_user_from_oidc(db, _data("s1"))
    assert e.value.status_code == 409
    assert db.query(User).count() == 1


def test_email_change_to_taken_address_keeps_old_email(db):
    make_user(db, username="other", email="taken@example.com")
    u = get_or_create_user_from_oidc(db, _data("s1", email="mine@example.com"))
    u2 = get_or_create_user_from_oidc(db, _data("s1", email="taken@example.com"))
    assert u2.id == u.id and u2.email == "mine@example.com"


def test_empty_email_does_not_collide(db):
    u1 = get_or_create_user_from_oidc(db, _data("s1", username="a", email=""))
    u2 = get_or_create_user_from_oidc(db, _data("s2", username="b", email=""))
    assert u1.email != u2.email


# ------------------------------------------------------------------ F-026

def test_login_rate_limited(client, db, monkeypatch):
    monkeypatch.setattr(settings, "oidc_rate_limit", "3/minute")
    codes = [_login(client).status_code for _ in range(5)]
    assert codes[:3] == [307] * 3 or all(c in (302, 307) for c in codes[:3])
    assert codes[3] == 429


def test_callback_rate_limited(client, db, monkeypatch):
    monkeypatch.setattr(settings, "oidc_rate_limit", "2/minute")
    codes = [client.get("/api/auth/callback?code=c&state=nope").status_code for _ in range(4)]
    assert codes[:2] == [400, 400] and codes[3] == 429


def test_pending_states_capped(client, db, monkeypatch):
    monkeypatch.setattr(settings, "oidc_max_pending_states", 2)
    monkeypatch.setattr(settings, "oidc_rate_limit", "100/minute")
    assert [_login(client).status_code in (302, 307) for _ in range(2)] == [True, True]
    r = _login(client)
    assert r.status_code == 503 and r.headers.get("retry-after")
    assert db.query(OidcPkceState).count() == 2


def test_expired_states_purged_before_cap(client, db, monkeypatch):
    monkeypatch.setattr(settings, "oidc_max_pending_states", 1)
    db.add(OidcPkceState(state="old", code_verifier="v", created_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
    db.commit()
    assert _login(client).status_code in (302, 307)


@pytest.fixture
def fresh_discovery(monkeypatch):
    monkeypatch.setattr(ra, "_oidc_config_cache", None)
    monkeypatch.setattr(ra, "_oidc_config_fetched_at", None)
    monkeypatch.setattr(ra, "_oidc_failed_at", None, raising=False)


def _discovery_client(exc=None, calls=None):
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            if calls is not None:
                calls.append(1)
            if exc:
                raise exc
            return _Resp(200, CFG)
    return C


@pytest.mark.parametrize("exc,code", [
    (httpx.ReadTimeout("slow"), 504),
    (httpx.ConnectError("down"), 502),
])
def test_discovery_errors_mapped(client, db, fresh_discovery, exc, code):
    with patch.object(ra.httpx, "AsyncClient", _discovery_client(exc)):
        assert client.get("/api/auth/login", follow_redirects=False).status_code == code


def test_discovery_failure_negative_cached(client, db, fresh_discovery):
    calls = []
    with patch.object(ra.httpx, "AsyncClient", _discovery_client(httpx.ConnectError("down"), calls)):
        client.get("/api/auth/login", follow_redirects=False)
        r = client.get("/api/auth/login", follow_redirects=False)
    assert len(calls) == 1
    assert r.status_code == 503 and r.headers.get("retry-after")
    assert db.query(OidcPkceState).count() == 0


def test_discovery_5xx_mapped_502(client, db, fresh_discovery):
    class C(_discovery_client()):
        async def get(self, *a, **k):
            return _Resp(500, {})
    with patch.object(ra.httpx, "AsyncClient", C):
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 502


@pytest.mark.parametrize("kw,code", [
    ({"post_exc": httpx.ReadTimeout("slow")}, 504),
    ({"post_exc": httpx.ConnectError("down")}, 502),
    ({"token": _Resp(503, {})}, 502),
    ({"userinfo": _Resp(500, {})}, 502),
    ({"get_exc": httpx.ReadTimeout("slow")}, 504),
])
def test_callback_idp_errors_mapped(client, db, kw, code):
    r = _login(client)
    assert _callback(client, _state_of(r)["state"][0], _binder(r), _fake_client(**kw)).status_code == code


def test_idp_timeouts_are_bounded():
    t = ra._idp_timeout()
    assert t.connect <= 5 and t.read <= settings.oidc_http_timeout_s
