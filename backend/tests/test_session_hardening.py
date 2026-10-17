"""Session hardening (spec 007): F-008 code exchange instead of JWT in URL,
F-007 token revocation via users.token_version."""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.auth import create_access_token
from app.config import settings
from app.models import LoginCode, User, UserRole
from tests.conftest import auth_headers, make_user


def _events(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.security"]


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _callback(client, groups, sub="sub-1"):
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp(200, {"access_token": "t"})
        async def get(self, *a, **k):
            return _Resp(200, {"sub": sub, "email": "s@example.com", "preferred_username": "s",
                               settings.oidc_groups_claim: groups})

    from app.routers import auth as ra
    cfg = {"token_endpoint": "http://x/t", "userinfo_endpoint": "http://x/u"}
    with patch.object(ra, "_oidc_config", new=AsyncMock(return_value=cfg)), \
         patch.object(ra.httpx, "AsyncClient", FakeClient), \
         patch.object(ra, "_consume_pkce_state", lambda db, st, b=None: "v"):
        return client.get("/api/auth/callback?code=c&state=st", follow_redirects=False)


def _code_from(resp):
    loc = resp.headers["location"]
    assert "#code=" in loc
    return loc.split("#code=", 1)[1]


# ---------------------------------------------------------------- F-008

def test_callback_redirects_with_fragment_code_not_jwt(client):
    r = _callback(client, [settings.oidc_user_group])
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith(settings.app_base_url.rstrip("/") + "/auth/token#code=")
    assert "?" not in loc and "eyJ" not in loc and "token=" not in loc
    assert len(_code_from(r)) >= 43


def test_exchange_returns_token_once(client, db):
    code = _code_from(_callback(client, [settings.oidc_user_group]))
    r = client.post("/api/auth/exchange", json={"code": code})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/api/auth/exchange", json={"code": code}).status_code == 400


def test_only_hash_is_stored(client, db):
    code = _code_from(_callback(client, [settings.oidc_user_group]))
    rows = db.query(LoginCode).all()
    assert len(rows) == 1
    assert rows[0].code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert code not in rows[0].code_hash


def test_expired_code_rejected(client, db, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    code = _code_from(_callback(client, [settings.oidc_user_group]))
    row = db.query(LoginCode).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    r = client.post("/api/auth/exchange", json={"code": code})
    assert r.status_code == 400
    msgs = _events(caplog)
    assert any("event=login_exchange" in m and "outcome=failed" in m for m in msgs)
    assert not any(code in m for m in msgs)


@pytest.mark.parametrize("bad", ["", "nope", "x" * 5000, "../../etc"])
def test_unknown_or_malformed_code_gives_same_400(client, bad):
    r = client.post("/api/auth/exchange", json={"code": bad})
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert r.json()["detail"] == "Invalid or expired code"


def test_missing_code_field_422(client):
    assert client.post("/api/auth/exchange", json={}).status_code == 422


def test_code_of_inactive_user_rejected(client, db):
    code = _code_from(_callback(client, [settings.oidc_user_group]))
    u = db.query(User).one()
    u.is_active = False
    db.commit()
    assert client.post("/api/auth/exchange", json={"code": code}).status_code == 400


def test_exchange_rate_limited(client):
    codes = [client.post("/api/auth/exchange", json={"code": f"c{i}"}).status_code for i in range(15)]
    assert 429 in codes


# ---------------------------------------------------------------- F-007

def test_logout_revokes_token(client, db, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    u = make_user(db)
    h = auth_headers(u)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    msgs = _events(caplog)
    assert any("event=logout" in m for m in msgs)
    assert not any(h["Authorization"].split()[1] in m for m in msgs)


def test_logout_without_or_with_bad_token_is_ok_and_noop(client, db):
    u = make_user(db)
    h = auth_headers(u)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/logout", headers={"Authorization": "Bearer junk"}).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 200


def test_token_without_version_claim_rejected(client, db):
    u = make_user(db)
    t = create_access_token({"sub": str(u.id), "role": u.role.value})
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_new_signin_keeps_other_session(client, db):
    first = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_user_group]))})
    second = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_user_group]))})
    for r in (first, second):
        t = r.json()["access_token"]
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 200


def test_admin_role_change_revokes_target_tokens(client, db):
    admin = make_user(db, role=UserRole.admin, username="adm", email="a@example.com")
    target = make_user(db, username="tgt", email="t@example.com")
    old = auth_headers(target)
    r = client.patch(f"/api/admin/users/{target.id}/role", json={"role": "manager"}, headers=auth_headers(admin))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401
    db.refresh(target)
    assert client.get("/api/auth/me", headers=auth_headers(target)).status_code == 200
    # the acting admin's own session is unaffected
    assert client.get("/api/auth/me", headers=auth_headers(admin)).status_code == 200


def test_signin_with_changed_role_revokes_old_tokens(client, db):
    t1 = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_user_group]))}).json()["access_token"]
    t2 = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_admin_group]))}).json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t1}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t2}"}).status_code == 200


def test_refused_idp_signin_revokes_and_keeps_row(client, db):
    t = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_user_group]))}).json()["access_token"]
    h = {"Authorization": f"Bearer {t}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    r = _callback(client, [])
    assert r.status_code == 403
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert db.query(User).filter(User.oidc_sub == "sub-1").count() == 1
    # back in a group: access returns without manual action
    t3 = client.post("/api/auth/exchange", json={"code": _code_from(_callback(client, [settings.oidc_user_group]))}).json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t3}"}).status_code == 200


def test_superadmin_login_token_carries_version(client, db, monkeypatch):
    monkeypatch.setattr(settings, "superadmin_user", "root")
    monkeypatch.setattr(settings, "superadmin_password", "a-long-test-password-123")  # gitleaks:allow
    r = client.post("/api/auth/superadmin-login", json={"username": "root", "password": "a-long-test-password-123"})  # gitleaks:allow
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    client.post("/api/auth/logout", headers=h)
    assert client.get("/api/auth/me", headers=h).status_code == 401
