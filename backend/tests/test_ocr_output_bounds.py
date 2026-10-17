"""F-029: LLM / OCR-service output is validated and bounded; malformed OCR response -> 502."""
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.services import llm_client, ocr_pipeline
from tests.conftest import auth_headers, make_user
from tests.test_uploads import _MINIMAL_JPEG


def _resp(content: bytes, status=200):
    return httpx.Response(status, content=content, request=httpx.Request("POST", "http://ocr.test/scan"))


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(_MINIMAL_JPEG)
    return p


@pytest.fixture
def ocr_on(monkeypatch):
    monkeypatch.setattr("app.config.settings.ocr_url", "http://ocr.test")


def _scan_with(body: bytes, img):
    with patch("app.services.ocr_pipeline.httpx.post", return_value=_resp(body)):
        return ocr_pipeline._ocr_service_scan(img)


# ── sanitizers ───────────────────────────────────────────────────────────────

def test_reading_digits_capped():
    assert ocr_pipeline.sanitize_reading("123456789012") == "123456789012"
    assert ocr_pipeline.sanitize_reading("1234567890123") is None
    assert ocr_pipeline.sanitize_reading("9" * 5_000_000) is None
    assert ocr_pipeline.sanitize_reading(["12345"]) is None
    assert ocr_pipeline.sanitize_reading("12") is None


def test_serial_charset_and_length():
    assert ocr_pipeline.sanitize_serial("1HLY0200026991") == "1HLY0200026991"
    assert ocr_pipeline.sanitize_serial("A" * 64) == "A" * 64
    assert ocr_pipeline.sanitize_serial("A" * 65) is None
    assert ocr_pipeline.sanitize_serial("<img src=x onerror=alert(1)>") is None
    assert ocr_pipeline.sanitize_serial({"a": 1}) is None
    assert ocr_pipeline.sanitize_serial("null") is None


def test_raw_texts_bounded():
    raw = [{"text": "t" * 1000, "conf": 0.5}] * 500 + ["str", {"text": 5}, {"text": "ok", "conf": "x"}]
    out = ocr_pipeline.sanitize_raw_texts(raw)
    assert len(out) <= 50
    assert all(len(e["text"]) <= 200 and 0 <= e["conf"] <= 1 for e in out)
    assert ocr_pipeline.sanitize_raw_texts("nope") == []


# ── OCR service response ─────────────────────────────────────────────────────

def test_ocr_reading_equal_to_serial_dropped(img):
    out = _scan_with(json.dumps({"detected_value": "24519788", "detected_serial": "24519788", "raw_texts": []}).encode(), img)
    assert out["detected_value"] is None
    assert out["detected_serial"] == "24519788"


def test_ocr_values_bounded(img):
    body = {"detected_value": "1" * 100, "detected_serial": "S" * 500,
            "raw_texts": [{"text": "a", "conf": 0.1}] * 1000, "detection_method": "<script>"}
    out = _scan_with(json.dumps(body).encode(), img)
    assert out["detected_value"] is None and out["detected_serial"] is None
    assert len(out["raw_texts"]) <= 50
    assert out["detection_method"] in ("ocr", "llm")


@pytest.mark.parametrize("body", [
    b"<html>garbage</html>",
    b"[" * 100_000,
    b'{"detected_value": ' + b"[" * 100_000,
    b"[1,2,3]",
    b"null",
    b'{"raw_texts": "' + b"x" * 200_000 + b'"}',
])
def test_ocr_malformed_raises_service_error(img, body):
    with pytest.raises(ocr_pipeline.OcrServiceError):
        _scan_with(body, img)


def test_ocr_serial_malformed_raises_service_error(img):
    with patch("app.services.ocr_pipeline.httpx.post", return_value=_resp(b"not json")):
        with pytest.raises(ocr_pipeline.OcrServiceError):
            ocr_pipeline._ocr_service_serial(img)


def test_ocr_serial_value_bounded(img):
    with patch("app.services.ocr_pipeline.httpx.post", return_value=_resp(json.dumps({"detected_serial": "<b>x</b>"}).encode())):
        assert ocr_pipeline._ocr_service_serial(img) is None


@pytest.mark.parametrize("path,kw", [("/api/ocr/scan", "scan")])
def test_scan_malformed_ocr_response_is_502(client, db, tmp_path, monkeypatch, ocr_on, path, kw):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    user = make_user(db)
    with patch("app.services.ocr_pipeline.httpx.post", return_value=_resp(b"{" * 50_000)):
        r = client.post(path, data={"engine": "ocr"}, files={"file": ("a.jpg", _MINIMAL_JPEG, "image/jpeg")},
                        headers=auth_headers(user))
    assert r.status_code == 502
    assert list(tmp_path.iterdir()) == []


def test_bulk_malformed_ocr_response_is_error_item(client, db, tmp_path, monkeypatch, ocr_on):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    user = make_user(db)
    with patch("app.services.ocr_pipeline.httpx.post", return_value=_resp(b"garbage")):
        r = client.post("/api/ocr/bulk-scan", data={"engine": "ocr"},
                        files=[("files", ("a.jpg", _MINIMAL_JPEG, "image/jpeg"))], headers=auth_headers(user))
    assert r.status_code == 200
    item = json.loads(next(l for l in r.text.splitlines() if l.startswith("data: "))[6:])
    assert item["error"] and item["detected_value"] is None


# ── LLM path ─────────────────────────────────────────────────────────────────

def _llm(monkeypatch, text, img):
    monkeypatch.setattr(llm_client, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm_client, "complete", lambda *a, **k: text)
    return ocr_pipeline._llm_fallback(img)


def test_llm_huge_reading_and_serial_dropped(monkeypatch, img):
    text = json.dumps({"reading": "1" * 100_000, "serial": "S" * 100_000})
    assert _llm(monkeypatch, text, img) == (None, None)


def test_llm_non_object_json(monkeypatch, img):
    assert _llm(monkeypatch, "[1,2]", img) == (None, None)
    assert _llm(monkeypatch, json.dumps({"reading": ["1"], "serial": {"a": 1}}), img) == (None, None)


def test_llm_serial_markup_dropped(monkeypatch, img):
    assert _llm(monkeypatch, json.dumps({"reading": "12345.6", "serial": "<img onerror=x>"}), img) == ("12345.6", None)


def test_gateway_text_capped(monkeypatch):
    big = "x" * 100_000
    monkeypatch.setattr(llm_client, "_gateway_headers", lambda: {})
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": big}}]}))
    c = httpx.Client(transport=transport)
    monkeypatch.setattr(llm_client.httpx, "post", lambda url, **kw: c.post(url, **kw))
    monkeypatch.setattr(llm_client.settings, "gateway_base_url", "http://gw.test/v1")
    text, _ = llm_client.gateway_complete("b64", "p", llm_client.get_profile())
    assert len(text) <= 8192


# ── EXIF ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,exp", [
    ("2025:12:24 14:30:59", "2025-12-24T14:30"),
    ("2025:12:24", "2025-12-24"),
    ("<script>alert(1)</script>xxxxxxxxxxxx", None),
    ("AAAA:BB:CC DD:EE:FF", None),
])
def test_exif_date_pattern(monkeypatch, tmp_path, val, exp):
    from PIL import Image
    p = tmp_path / "e.jpg"
    im = Image.new("RGB", (4, 4))
    ex = Image.Exif()
    ex[306] = val
    im.save(p, exif=ex)
    assert ocr_pipeline._extract_exif_datetime(p) == exp
