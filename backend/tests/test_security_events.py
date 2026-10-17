"""Security events and access-log redaction (spec 006, F-010, F-006)."""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.auth import get_or_create_user_from_oidc
from app.config import settings
from app.models import User, UserRole
from tests.conftest import auth_headers, make_user

SA_PW = "Zr7!kQ2mVx9pLw"


def events(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.security"]


@pytest.fixture
def sa(monkeypatch):
    monkeypatch.setattr(settings, "superadmin_user", "sadmin")
    monkeypatch.setattr(settings, "superadmin_password", SA_PW)


def test_superadmin_login_ok_event(client, sa, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    r = client.post("/api/auth/superadmin-login", json={"username": "sadmin", "password": SA_PW})
    assert r.status_code == 200
    msgs = events(caplog)
    assert any("event=superadmin_login" in m and "outcome=ok" in m for m in msgs)
    assert not any(SA_PW in m for m in msgs)


def test_superadmin_login_failed_event_has_no_credentials(client, sa, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    r = client.post("/api/auth/superadmin-login", json={"username": "sadmin", "password": "wrong-pass-xyz"})
    assert r.status_code == 401
    msgs = events(caplog)
    assert any("event=superadmin_login" in m and "outcome=failed" in m and "reason=" in m for m in msgs)
    assert not any("wrong-pass-xyz" in m or SA_PW in m for m in msgs)


def test_superadmin_login_disabled_event(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    monkeypatch.setattr(settings, "superadmin_password", "")
    assert client.post("/api/auth/superadmin-login", json={"username": "a", "password": "b"}).status_code == 401
    assert any("outcome=failed" in m and "reason=not_configured" in m for m in events(caplog))


def test_superadmin_login_reasserts_role(client, db, sa):
    u = User(username="sadmin", email="sadmin@localhost", role=UserRole.user, is_active=True)
    db.add(u)
    db.commit()
    r = client.post("/api/auth/superadmin-login", json={"username": "sadmin", "password": SA_PW})
    assert r.status_code == 200
    db.refresh(u)
    assert u.role == UserRole.superadmin
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["role"] == "superadmin"


def test_superadmin_login_reactivates_inactive_is_not_done(client, db, sa):
    # an inactive superadmin stays inactive: the token is then rejected by get_current_user
    u = User(username="sadmin", email="sadmin@localhost", role=UserRole.superadmin, is_active=False)
    db.add(u)
    db.commit()
    r = client.post("/api/auth/superadmin-login", json={"username": "sadmin", "password": SA_PW})
    if r.status_code == 200:
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
        assert me.status_code == 401


def test_sso_rejected_event_has_reason_only(db, caplog):
    from fastapi import HTTPException
    caplog.set_level(logging.INFO, logger="app.security")
    with pytest.raises(HTTPException):
        get_or_create_user_from_oidc(db, {"sub": "s1", "email": "secret.person@example.com",
                                          settings.oidc_groups_claim: ["super-secret-group"]})
    msgs = events(caplog)
    assert any("event=sso_login" in m and "outcome=denied" in m and "reason=no_permitted_group" in m for m in msgs)
    joined = " ".join(msgs)
    assert "super-secret-group" not in joined and "secret.person" not in joined and "example.com" not in joined


def test_callback_invalid_state_event(client, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    r = client.get("/api/auth/callback?code=THECODE&state=THESTATE", follow_redirects=False)
    assert r.status_code == 400
    msgs = events(caplog)
    assert any("event=login" in m and "outcome=failed" in m and "reason=invalid_state" in m for m in msgs)
    assert not any("THECODE" in m or "THESTATE" in m for m in msgs)


def test_callback_login_ok_event(client, db, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    from app.routers import auth as ra

    class Resp:
        def __init__(self, status, data):
            self.status_code, self._d = status, data
        def json(self):
            return self._d
        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return Resp(200, {"access_token": "t"})
        async def get(self, *a, **k):
            return Resp(200, {"sub": "sub-ok", "email": "o@example.com", "preferred_username": "o",
                              settings.oidc_groups_claim: [settings.oidc_user_group]})

    cfg = {"token_endpoint": "http://x/t", "userinfo_endpoint": "http://x/u"}
    with patch.object(ra, "_oidc_config", new=AsyncMock(return_value=cfg)), \
         patch.object(ra.httpx, "AsyncClient", FakeClient), \
         patch.object(ra, "_consume_pkce_state", lambda db, st: "v"):
        r = client.get("/api/auth/callback?code=c&state=st", follow_redirects=False)
    assert r.status_code in (302, 307)
    msgs = events(caplog)
    assert any("event=login" in m and "outcome=ok" in m for m in msgs)
    assert not any("o@example.com" in m for m in msgs)


def test_401_no_credentials_event(client, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    assert client.get("/api/auth/me").status_code == 401
    assert any("event=auth_denied" in m and "outcome=denied" in m and "reason=no_credentials" in m
               for m in events(caplog))


def test_401_invalid_token_event_does_not_log_token(client, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    msgs = events(caplog)
    assert any("event=auth_denied" in m and "reason=invalid_token" in m for m in msgs)
    assert not any("abc.def.ghi" in m for m in msgs)


def test_401_inactive_user_event(client, db, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    u = make_user(db)
    u.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers=auth_headers(u)).status_code == 401
    assert any("event=auth_denied" in m and "reason=user_inactive_or_unknown" in m for m in events(caplog))


def test_403_role_denied_event(client, db, caplog):
    caplog.set_level(logging.INFO, logger="app.security")
    u = make_user(db, role=UserRole.user)
    r = client.get("/api/admin/users", headers=auth_headers(u))
    assert r.status_code == 403
    msgs = events(caplog)
    assert any("event=auth_denied" in m and "reason=insufficient_role" in m and f"actor={u.id}" in m for m in msgs)


# ── access-log redaction ──────────────────────────────────────────────────────

def _record(args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d', args, None)


def test_redaction_filter_masks_code_and_state():
    from app.main import AccessLogRedactionFilter
    f = AccessLogRedactionFilter()
    rec = _record(("1.2.3.4:5", "GET", "/api/auth/callback?code=SECRETCODE&state=SECRETSTATE&x=1", "1.1", 302))
    assert f.filter(rec) is True
    out = rec.getMessage()
    assert "SECRETCODE" not in out and "SECRETSTATE" not in out
    assert "code=[redacted]" in out and "state=[redacted]" in out and "x=1" in out


def test_redaction_filter_leaves_other_params_and_paths():
    from app.main import AccessLogRedactionFilter
    rec = _record(("h", "GET", "/api/meters?barcode=1&statement=2", "1.1", 200))
    AccessLogRedactionFilter().filter(rec)
    assert "barcode=1" in rec.getMessage() and "statement=2" in rec.getMessage()


def test_redaction_filter_installed_on_uvicorn_access():
    import app.main  # noqa: F401
    from app.main import AccessLogRedactionFilter
    assert any(isinstance(f, AccessLogRedactionFilter) for f in logging.getLogger("uvicorn.access").filters)
