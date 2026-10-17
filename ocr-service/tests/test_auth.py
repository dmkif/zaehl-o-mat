"""F-030: bearer-token authentication of the OCR service."""
import io
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from tests.test_endpoints import _JPEG, _MOCK_SCAN_RESULT

KEY = "k" * 40  # gitleaks:allow


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _post(client, path="/scan", headers=None):
    with patch("app.main._run_ocr_on_file_sync", return_value=_MOCK_SCAN_RESULT), \
         patch("app.main._extract_serial_sync", return_value="123"):
        return client.post(path, files={"file": ("a.jpg", io.BytesIO(_JPEG), "image/jpeg")}, headers=headers or {})


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(settings, "ocr_api_key", KEY)


@pytest.mark.parametrize("path", ["/scan", "/serial"])
class TestWithKey:
    def test_missing_header_401(self, client, keyed, path):
        r = _post(client, path)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize("hdr", ["Bearer wrong", "Basic " + "k" * 40, KEY, "Bearer ", b"Bearer \xfcn\xefcode"])
    def test_wrong_header_401(self, client, keyed, path, hdr):
        assert _post(client, path, {"Authorization": hdr}).status_code == 401

    def test_correct_key_ok(self, client, keyed, path):
        assert _post(client, path, {"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_health_open_with_key(client, keyed):
    assert client.get("/health").status_code == 200


def test_no_key_stays_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(settings, "ocr_api_key", None)
    assert _post(client).status_code == 200


def test_empty_key_counts_as_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "ocr_api_key", "")
    assert _post(client).status_code == 200


def test_startup_warning_without_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ocr_api_key", None)
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            pass
    assert any("OCR_API_KEY" in r.getMessage() for r in caplog.records)


def test_no_startup_warning_with_key(keyed, caplog):
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            pass
    assert not any("OCR_API_KEY" in r.getMessage() for r in caplog.records)
