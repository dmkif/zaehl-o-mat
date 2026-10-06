"""
Endpoint behaviour for the gateway path: 503/502 mapping, bulk-scan fail-fast,
health, log hygiene (spec 012 US1/US2/US4, SC-004/SC-005).
All LLM calls are mocked at _llm_fallback / the HTTP layer.
"""
import json
import logging
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from app.services import llm_client
from tests.conftest import auth_headers, make_user
from tests.test_ocr_endpoint import _JPEG

KEY = "sk-test-secret-key"
UNAVAILABLE = "Lokales Modell nicht erreichbar, bitte später erneut versuchen"
CONFIG = "Konfigurationsfehler beim KI-Gateway"


@pytest.fixture(autouse=True)
def gateway(monkeypatch, tmp_path):
    s = llm_client.settings
    monkeypatch.setattr(s, "ocr_backend", "gateway")
    monkeypatch.setattr(s, "gateway_base_url", "http://gw.test/v1")
    monkeypatch.setattr(s, "gateway_api_key", SecretStr(KEY))
    monkeypatch.setattr(s, "gateway_profile", "local-only")
    monkeypatch.setattr(s, "model_profile", "")
    monkeypatch.setattr(s, "ollama_url", "")
    monkeypatch.setattr(s, "ocr_url", "")
    monkeypatch.setattr(s, "upload_path", str(tmp_path))


def scan(client, user, engine="auto"):
    return client.post(
        "/api/ocr/scan",
        data={"engine": engine},
        files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
        headers=auth_headers(user),
    )


def test_scan_ok_over_gateway(client, db):
    user = make_user(db)
    with patch("app.routers.ocr._llm_fallback", return_value=("031009.9", "1HLY0200026991")):
        r = scan(client, user)
    assert r.status_code == 200
    body = r.json()
    assert body["detected_value"] == "031009.9" and body["detection_method"] == "llm"


@pytest.mark.parametrize("engine", ["auto", "llm"])
def test_unavailable_gives_503_with_retry_text(client, db, engine):
    user = make_user(db)
    with patch("app.routers.ocr._llm_fallback", side_effect=llm_client.LocalModelUnavailable()):
        r = scan(client, user, engine)
    assert r.status_code == 503
    assert r.json()["detail"] == UNAVAILABLE


def test_config_error_gives_502(client, db):
    user = make_user(db)
    with patch("app.routers.ocr._llm_fallback", side_effect=llm_client.GatewayConfigError()):
        r = scan(client, user)
    assert r.status_code == 502
    assert r.json()["detail"] == CONFIG


def test_engine_llm_without_gateway_url_is_503(client, db, monkeypatch):
    monkeypatch.setattr(llm_client.settings, "gateway_base_url", "")
    r = scan(client, make_user(db), "llm")
    assert r.status_code == 503
    assert "GATEWAY_BASE_URL" in r.json()["detail"]


def test_no_cloud_or_ollama_fallback_via_http(client, db, monkeypatch):
    """End to end through the HTTP layer: one request, to the gateway only."""
    monkeypatch.setattr(llm_client.settings, "ollama_url", "http://ollama.test:11434")
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(503)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm_client.httpx, "post", lambda url, **kw: c.post(url, **kw))
    r = scan(client, make_user(db))
    assert r.status_code == 503 and r.json()["detail"] == UNAVAILABLE
    assert hosts == ["gw.test"]


def _bulk(client, user, n=3):
    r = client.post(
        "/api/ocr/bulk-scan",
        files=[("files", (f"m{i}.jpg", _JPEG + bytes([i]), "image/jpeg")) for i in range(n)],
        headers=auth_headers(user),
    )
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    return [e for e in events if "original_filename" in e]


def test_bulk_unavailable_stops_after_first_image(client, db):
    user = make_user(db)
    calls = []

    def fail(*a, **k):
        calls.append(1)
        raise llm_client.LocalModelUnavailable()

    with patch("app.routers.ocr._llm_fallback", fail):
        items = _bulk(client, user, 3)
    assert len(calls) == 1
    assert [i["error"] for i in items] == [UNAVAILABLE] * 3


def test_bulk_config_error_text(client, db):
    user = make_user(db)
    with patch("app.routers.ocr._llm_fallback", side_effect=llm_client.GatewayConfigError()):
        items = _bulk(client, user, 2)
    assert [i["error"] for i in items] == [CONFIG] * 2


def test_health_reports_gateway(client, monkeypatch):
    monkeypatch.setattr(llm_client, "gateway_healthy", lambda: True)
    body = client.get("/api/health").json()
    assert body["llm"] is True and body["llm_model"] == "qwen3.5-9b"
    monkeypatch.setattr(llm_client, "gateway_healthy", lambda: False)
    assert client.get("/api/health").json()["llm"] is False


# ── Log hygiene (SC-005, FR-009) ──────────────────────────────────────────────

SECRET_VALUES = ["031009.9", "1HLY0200026991"]


@pytest.mark.parametrize("outcome", ["ok", "unavailable", "config"])
def test_logs_and_errors_contain_no_secrets(client, db, caplog, monkeypatch, tmp_path, outcome):
    from PIL import Image

    img = tmp_path / "m.jpg"
    Image.new("RGB", (400, 200), "white").save(img, "JPEG")
    big = img.read_bytes()

    def handler(request):
        if outcome == "ok":
            return httpx.Response(200, json={"model": "Qwen3.5-9B-GGUF", "choices": [
                {"message": {"content": '{"reading": "031009.9", "serial": "1HLY0200026991"}'}}]})
        return httpx.Response(503 if outcome == "unavailable" else 401)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm_client.httpx, "post", lambda url, **kw: c.post(url, **kw))
    user = make_user(db)
    with caplog.at_level(logging.DEBUG):
        r = client.post(
            "/api/ocr/scan",
            data={"engine": "auto"},
            files={"file": ("meter.jpg", big, "image/jpeg")},
            headers=auth_headers(user),
        )
    assert r.status_code == {"ok": 200, "unavailable": 503, "config": 502}[outcome]
    haystack = caplog.text + r.text if outcome != "ok" else caplog.text
    assert KEY not in caplog.text and KEY not in r.text
    assert "data:image" not in caplog.text and "base64" not in caplog.text.lower()
    import base64 as b64
    assert b64.b64encode(big).decode()[:60] not in caplog.text
    for secret in SECRET_VALUES:
        assert secret not in caplog.text
    assert "Bearer" not in haystack
