"""Feature 010: F-031 docs/health, F-046 request id, F-027 oil key redaction,
F-025 guarded startup, readiness independent of optional services."""
import asyncio
import logging
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.models import UserRole
from tests.conftest import auth_headers, make_user


# ------------------------------------------------------------------ F-031

def test_docs_disabled_by_default(client):
    for p in ("/api/docs", "/api/redoc", "/api/openapi.json"):
        assert client.get(p).status_code == 404


def test_docs_enabled_by_setting():
    code = ("import os;os.environ['ENABLE_API_DOCS']='true';os.environ.setdefault('DATABASE_URL','sqlite://');"
            "from app.main import app;print(app.docs_url, app.redoc_url, app.openapi_url)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["/api/docs", "/api/redoc", "/api/openapi.json"]


def test_anonymous_health_only_status(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_anonymous_health_makes_no_outbound_calls(client, monkeypatch):
    monkeypatch.setattr(settings, "ocr_url", "http://ocr.invalid")
    monkeypatch.setattr(settings, "ollama_url", "http://ollama.invalid")
    with patch("httpx.get", side_effect=AssertionError("outbound call")):
        assert client.get("/api/health").status_code == 200


def test_health_503_when_scheduler_down(client):
    from app import main
    with patch.object(type(main.scheduler), "running", new=False):
        r = client.get("/api/health")
    assert r.status_code == 503 and r.json() == {"status": "degraded"}


def test_health_live_unchanged(client):
    assert client.get("/api/health/live").json() == {"status": "ok"}


def test_details_requires_admin(client, db):
    assert client.get("/api/health/details").status_code == 401
    u = make_user(db, role=UserRole.user)
    assert client.get("/api/health/details", headers=auth_headers(u)).status_code == 403


def test_details_for_admin_and_cached(client, db, monkeypatch):
    from app import main
    main._details_cache.clear()
    monkeypatch.setattr(settings, "ocr_url", "http://ocr.invalid")
    admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))
    with patch.object(main._httpx, "get", fake_get):
        r1 = client.get("/api/health/details", headers=auth_headers(admin))
        r2 = client.get("/api/health/details", headers=auth_headers(admin))
    body = r1.json()
    assert r1.status_code == 200 and body["db"] is True and body["ocr"] is True and "version" in body
    assert r2.json()["ocr"] is True
    assert len(calls) == 1  # second call served from the short cache


# ------------------------------------------------------------------ F-046

@pytest.mark.parametrize("rid,kept", [
    ("abc-123_X.y", True), ("a" * 64, True), ("a" * 65, False), ("a" * 8000, False),
    ("bad id", False), ("x;y", False),
])
def test_request_id_validated(client, rid, kept):
    try:
        rid.encode("latin-1")
    except UnicodeEncodeError:
        pytest.skip("not sendable as header")
    out = client.get("/api/health/live", headers={"X-Request-ID": rid}).headers["x-request-id"]
    assert (out == rid) if kept else (out != rid and len(out) <= 64)


def test_request_id_generated_when_absent(client):
    assert 0 < len(client.get("/api/health/live").headers["x-request-id"]) <= 64


# ------------------------------------------------------------------ F-027

KEY = "SECRETKEY123456"  # gitleaks:allow


def test_error_text_never_contains_key():
    from app.services.oil_price import _safe_error
    req = httpx.Request("GET", f"https://x.example/list?apikey={KEY}&a=1")
    exc = httpx.HTTPStatusError(f"boom {req.url}", request=req, response=httpx.Response(403, request=req))
    s = _safe_error(exc)
    assert KEY not in s and "403" in s
    assert KEY not in _safe_error(httpx.ConnectError(f"cannot reach {req.url}"))


def test_tankerkoenig_key_sent_as_param_not_in_log(db, monkeypatch, caplog):
    from app.services import oil_price
    monkeypatch.setattr(settings, "oil_price_source", "tankerkoenig")
    monkeypatch.setattr(settings, "oil_price_api_key", KEY)
    seen = {}

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k):
            seen["url"], seen["params"] = url, k.get("params")
            req = httpx.Request("GET", f"{url}?apikey={KEY}")
            raise httpx.HTTPStatusError(f"bad {req.url}", request=req, response=httpx.Response(500, request=req))
    caplog.set_level(logging.DEBUG)
    with patch.object(oil_price.httpx, "AsyncClient", C):
        assert asyncio.run(oil_price.fetch_and_store_oil_price(db)) is None
    assert KEY not in seen["url"]
    assert KEY not in caplog.text


def test_logging_filter_redacts_apikey():
    from app.main import AccessLogRedactionFilter
    rec = logging.LogRecord("x", logging.INFO, "", 0, "GET %s", (f"/x?apikey={KEY}&code=1",), None)
    rec2 = logging.LogRecord("x", logging.INFO, "", 0, f"GET /x?apikey={KEY}&b=2", (), None)
    for r in (rec, rec2):
        AccessLogRedactionFilter().filter(r)
        assert KEY not in r.getMessage()


# ------------------------------------------------------------------ F-025

@pytest.mark.parametrize("price", [float("nan"), float("inf"), 1e30, -5.0, 0.0])
def test_absurd_price_not_stored(db, monkeypatch, price):
    from app.services import oil_price
    monkeypatch.setattr(settings, "oil_price_source", "custom")
    with patch.object(oil_price, "_fetch_custom", new=AsyncMock(return_value=price)):
        assert asyncio.run(oil_price.fetch_and_store_oil_price(db)) is None


def test_db_error_in_oil_price_is_guarded(db, monkeypatch, caplog):
    from app.services import oil_price
    monkeypatch.setattr(settings, "oil_price_source", "custom")

    class Boom:
        def query(self, *a, **k): raise RuntimeError("db gone")
        def rollback(self): self.rolled = True
    b = Boom()
    with patch.object(oil_price, "_fetch_custom", new=AsyncMock(return_value=100.0)):
        assert asyncio.run(oil_price.fetch_and_store_oil_price(b)) is None
    assert getattr(b, "rolled", False)


def test_run_oil_price_never_raises(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    asyncio.run(main._run_oil_price())  # must not raise


def test_startup_survives_oil_price_failure(db):
    from fastapi.testclient import TestClient
    from app import main
    from app.database import get_db
    main.app.dependency_overrides[get_db] = lambda: iter([db])
    try:
        with patch("app.services.oil_price.fetch_and_store_oil_price", new=AsyncMock(side_effect=RuntimeError("x"))):
            with TestClient(main.app) as c:
                assert c.get("/api/health/live").status_code == 200
    finally:
        main.app.dependency_overrides.clear()
