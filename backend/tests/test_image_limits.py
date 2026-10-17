"""F-011: pixel/side limits are checked from the image header before anything is stored."""
import json
import struct
import zlib
from unittest.mock import AsyncMock, patch

import pytest

from app.models import UploadFile
from tests.conftest import auth_headers, make_user
from tests.test_uploads import _MINIMAL_JPEG

_OCR_PATCH = "app.routers.ocr._run_ocr_on_file"
_OCR_RESULT = {"raw_texts": [], "detected_value": "1", "detected_serial": None, "detection_method": "ocr"}


def _png_header(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    # IHDR + a tiny IDAT: a real bomb is small too, and PIL needs an IDAT to open the file.
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


def _gif_header(w: int, h: int) -> bytes:
    # logical screen + one image descriptor of the same size, no pixel data
    return b"GIF89a" + struct.pack("<HHBBB", w, h, 0, 0, 0) + b",\x00\x00\x00\x00" + struct.pack("<HHB", w, h, 0) + b"\x02\x00;"


def _bmp_header(w: int, h: int) -> bytes:
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, 0, 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 54 + 16, 0, 0, 54) + info + b"\x00" * 16


BOMBS = {
    "png-20000x20000": _png_header(20000, 20000),
    "gif-65535x65535": _gif_header(65535, 65535),
    "bmp-40000x40000": _bmp_header(40000, 40000),
    "png-side-13000x1": _png_header(13000, 1),
    "png-1x13000": _png_header(1, 13000),
}


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    return tmp_path


def _files(p):
    return [f for f in p.iterdir() if f.is_file()]


@pytest.mark.parametrize("name", list(BOMBS))
def test_scan_rejects_bomb_before_storing(client, db, uploads, name):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)) as ocr:
        r = client.post("/api/ocr/scan", data={"engine": "ocr"},
                        files={"file": ("x.img", BOMBS[name], "image/png")},
                        headers=auth_headers(user))
    assert r.status_code in (413, 422)
    assert _files(uploads) == []
    ocr.assert_not_called()
    assert db.query(UploadFile).count() == 0


@pytest.mark.parametrize("name", ["png-20000x20000", "gif-65535x65535"])
def test_scan_rejects_bomb_serial_file(client, db, uploads, name):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = client.post("/api/ocr/scan", data={"engine": "ocr"},
                        files={"file": ("m.jpg", _MINIMAL_JPEG, "image/jpeg"),
                               "serial_file": ("s.img", BOMBS[name], "image/png")},
                        headers=auth_headers(user))
    assert r.status_code in (413, 422)
    assert _files(uploads) == []


def test_bulk_scan_rejects_bomb_item_but_keeps_good_one(client, db, uploads):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = client.post("/api/ocr/bulk-scan", data={"engine": "ocr"},
                        files=[("files", ("bomb.png", BOMBS["png-20000x20000"], "image/png")),
                               ("files", ("ok.jpg", _MINIMAL_JPEG, "image/jpeg"))],
                        headers=auth_headers(user))
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and "original_filename" in line]
    bomb = next(e for e in events if e["original_filename"] == "bomb.png")
    good = next(e for e in events if e["original_filename"] == "ok.jpg")
    assert bomb["error"] and bomb["temp_image_path"] is None
    assert good["temp_image_path"]
    assert len(_files(uploads)) == 1


def test_normal_image_still_scans(client, db, uploads):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = client.post("/api/ocr/scan", data={"engine": "ocr"},
                        files={"file": ("m.jpg", _MINIMAL_JPEG, "image/jpeg")},
                        headers=auth_headers(user))
    assert r.status_code == 200


def test_limits_default_and_boundaries():
    from app.services.ocr_pipeline import ImageTooLarge, check_image_dimensions, image_limits

    assert image_limits() == (40_000_000, 12_000)
    check_image_dimensions(_png_header(6000, 6000))        # 36 MP, ok
    check_image_dimensions(_png_header(12000, 100))        # side == limit, ok
    with pytest.raises(ImageTooLarge):
        check_image_dimensions(_png_header(7000, 6000))    # 42 MP
    with pytest.raises(ImageTooLarge):
        check_image_dimensions(_png_header(12001, 1))
    with pytest.raises(ImageTooLarge):
        check_image_dimensions(b"\x89PNG\r\n\x1a\ngarbage")  # unparseable header is rejected too


@pytest.mark.parametrize("name,expected", [
    ("png-20000x20000", (20000, 20000)),
    ("gif-65535x65535", (65535, 65535)),
    ("bmp-40000x40000", (40000, 40000)),
])
def test_fixture_headers_are_really_parseable(name, expected):
    """The bomb fixtures must be rejected for their size, not because they are malformed."""
    import io
    from PIL import Image

    old, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        assert Image.open(io.BytesIO(BOMBS[name])).size == expected
    finally:
        Image.MAX_IMAGE_PIXELS = old
