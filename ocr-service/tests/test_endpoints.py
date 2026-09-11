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

# ── Minimal valid JPEG (1×1 pixel) ────────────────────────────────────────────
_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'"
    b"9=82<.342\x1edL\t\x10\x17\x19\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e"
    b"\x1e\x1e\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
    b"\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
    b"\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ"
    b"cdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95"
    b"\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca"
    b"\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7"
    b"\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00"
    b"\x08\x01\x01\x00\x00?\x00\xfb\xd3\xff\xd9"
)

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
