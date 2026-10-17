"""
HTTP-layer tests for the OCR service (POST /scan, POST /serial, GET /health).

Real EasyOCR pipeline calls are mocked so this suite runs without GPU
hardware or model weights — matching the existing backend test convention
(see backend/tests/test_ocr_endpoint.py).

Negative-input cases (corrupt image, missing file) exist here because
Constitution Principle X requires negative tests for any feature touching
input handling.
"""
import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# ── Valid JPEG (the service now reads the header for the F-011 size limit) ───
def _make_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, format="JPEG")
    return buf.getvalue()


_JPEG = _make_jpeg()

_MOCK_SCAN_RESULT = {
    "raw_texts": [{"text": "12345", "conf": 0.95}],
    "detected_value": "12345",
    "detected_serial": None,
    "detection_method": "ocr",
}


class TestScanEndpoint:
    def test_happy_path_returns_detected_value(self):
        with patch("app.main._run_ocr_on_file_sync", return_value=_MOCK_SCAN_RESULT):
            r = client.post("/scan", files={"file": ("meter.jpg", _JPEG, "image/jpeg")})
        assert r.status_code == 200
        body = r.json()
        assert body["detected_value"] == "12345"
        assert body["detection_method"] == "ocr"

    def test_corrupt_image_returns_400(self):
        r = client.post("/scan", files={"file": ("bad.jpg", b"not an image", "image/jpeg")})
        assert r.status_code == 400

    def test_missing_file_returns_422(self):
        r = client.post("/scan")
        assert r.status_code == 422

    def test_pipeline_failure_returns_500(self):
        with patch("app.main._run_ocr_on_file_sync", side_effect=RuntimeError("boom")):
            r = client.post("/scan", files={"file": ("meter.jpg", _JPEG, "image/jpeg")})
        assert r.status_code == 500


class TestSerialEndpoint:
    def test_happy_path_returns_detected_serial(self):
        with patch("app.main._extract_serial_sync", return_value="0012345678"):
            r = client.post("/serial", files={"file": ("serial.jpg", _JPEG, "image/jpeg")})
        assert r.status_code == 200
        assert r.json()["detected_serial"] == "0012345678"

    def test_corrupt_image_returns_400(self):
        r = client.post("/serial", files={"file": ("bad.jpg", b"not an image", "image/jpeg")})
        assert r.status_code == 400

    def test_missing_file_returns_422(self):
        r = client.post("/serial")
        assert r.status_code == 422


class TestHealthEndpoint:
    def test_returns_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
