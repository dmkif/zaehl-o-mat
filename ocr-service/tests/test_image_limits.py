"""F-011: the OCR service rejects oversized images from the header and caps its upscale."""
import struct
import zlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _png(w: int, h: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


def _gif(w: int, h: int) -> bytes:
    # logical screen + one image descriptor of the same size, no pixel data
    return b"GIF89a" + struct.pack("<HHBBB", w, h, 0, 0, 0) + b",\x00\x00\x00\x00" + struct.pack("<HHB", w, h, 0) + b"\x02\x00;"


def _bmp(w: int, h: int) -> bytes:
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, 0, 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 70, 0, 0, 54) + info + b"\x00" * 16


BOMBS = [_png(20000, 20000), _gif(65535, 65535), _bmp(40000, 40000), _png(13000, 1)]


@pytest.mark.parametrize("path", ["/scan", "/serial"])
@pytest.mark.parametrize("data", BOMBS)
def test_bomb_rejected_before_pipeline(path, data):
    with patch("app.main._run_ocr_on_file_sync") as scan, patch("app.main._extract_serial_sync") as serial:
        r = client.post(path, files={"file": ("x.img", data, "application/octet-stream")})
    assert r.status_code == 413
    scan.assert_not_called()
    serial.assert_not_called()


def test_normal_png_passes_limits():
    with patch("app.main._run_ocr_on_file_sync", return_value={"detected_value": "1"}):
        r = client.post("/scan", files={"file": ("x.png", _png(800, 600), "image/png")})
    assert r.status_code == 200


def test_upscale_is_capped():
    from app.pipeline import MAX_UPSCALED_SIDE, upscaled_size

    assert upscaled_size(100, 50, 4) == (400, 200)               # normal crop: full factor
    w, h = upscaled_size(12000, 8000, 4)                          # already_cropped huge image
    assert max(w, h) <= MAX_UPSCALED_SIDE
    assert w * 2 == pytest.approx(h * 3, abs=3)                   # aspect ratio kept
    assert upscaled_size(5000, 10, 3)[0] <= MAX_UPSCALED_SIDE
