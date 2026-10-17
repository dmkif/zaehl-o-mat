"""Feature 011: F-032 (undecodable images are not stored) and F-033 (GPS removed)."""
import hashlib
import io
import json
import struct
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.models import UploadFile, UserRole
from app.routers.ocr import _extract_exif_datetime
from tests.conftest import auth_headers, make_meter, make_property, make_user

GPS_IFD = 0x8825


def _exif_with_gps() -> Image.Exif:
    img = Image.new("RGB", (4, 4))
    ex = img.getexif()
    ex[274] = 6                       # orientation
    ex[271] = "SecretCam"            # make
    ex[306] = "2025:12:24 14:30:45"
    ex.get_ifd(0x8769)[36867] = "2025:12:24 14:30:45"
    gps = ex.get_ifd(GPS_IFD)
    gps[1], gps[2], gps[3], gps[4] = "N", (52.0, 31.0, 12.0), "E", (13.0, 24.0, 18.0)
    return ex


def _jpeg(size=(64, 48), exif=None, xmp=False) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif.tobytes()} if exif is not None else {}
    Image.effect_noise(size, 80).convert("RGB").save(buf, "JPEG", **kw)
    data = buf.getvalue()
    if xmp:
        body = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>GPSLatitude=52</x:xmpmeta>"
        seg = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
        data = data[:2] + seg + data[2:]
    return data


def _png(exif=None) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((32, 32), 80).convert("RGB").save(buf, "PNG", **({"exif": exif.tobytes()} if exif else {}))
    return buf.getvalue()


def _webp(exif=None) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((32, 32), 80).convert("RGB").save(buf, "WEBP", lossless=True, **({"exif": exif.tobytes()} if exif else {}))
    return buf.getvalue()


def _tiff() -> bytes:
    # PIL cannot write a GPS IFD into a TIFF; device tags stand in for "metadata that must go".
    buf = io.BytesIO()
    img = Image.effect_noise((32, 32), 80).convert("RGB")
    ex = Image.Exif()
    ex[271], ex[270], ex[274], ex[306] = "SecretCam", "home at 52.5N", 6, "2025:12:24 14:30:45"
    img.save(buf, "TIFF", exif=ex)
    return buf.getvalue()


def _gps_of(data: bytes):
    with Image.open(io.BytesIO(data)) as im:
        ex = im.getexif()
        return dict(ex.get_ifd(GPS_IFD)), ex, im


from app.services.image_hygiene import ImageUndecodable, sanitize_image  # noqa: E402


class TestSanitize:
    def test_jpeg_gps_removed_date_and_orientation_kept(self, tmp_path):
        data = _jpeg(exif=_exif_with_gps())
        assert _gps_of(data)[0]
        out = sanitize_image(data)
        gps, ex, _ = _gps_of(out)
        assert gps == {}
        assert GPS_IFD not in ex or not ex.get_ifd(GPS_IFD)
        assert ex.get(274) == 6
        assert ex.get(271) is None  # make/model etc. are dropped too
        f = tmp_path / "a.jpg"
        f.write_bytes(out)
        assert _extract_exif_datetime(f) == "2025-12-24T14:30"
        assert b"SecretCam" not in out

    def test_jpeg_pixels_untouched(self):
        data = _jpeg(exif=_exif_with_gps())
        out = sanitize_image(data)
        a = Image.open(io.BytesIO(data)).convert("RGB").tobytes()
        b = Image.open(io.BytesIO(out)).convert("RGB").tobytes()
        assert a == b

    def test_jpeg_xmp_removed(self):
        out = sanitize_image(_jpeg(xmp=True))
        assert b"GPSLatitude" not in out and b"xmpmeta" not in out

    def test_jpeg_without_metadata_unchanged_semantics(self):
        out = sanitize_image(_jpeg())
        Image.open(io.BytesIO(out)).load()

    def test_deterministic(self):
        data = _jpeg(exif=_exif_with_gps())
        assert sanitize_image(data) == sanitize_image(data)

    def test_png_exif_and_text_chunks(self):
        data = _png(_exif_with_gps())
        assert _gps_of(data)[0]
        out = sanitize_image(data)
        assert _gps_of(out)[0] == {}
        assert b"SecretCam" not in out
        Image.open(io.BytesIO(out)).load()

    def test_webp_exif(self):
        data = _webp(_exif_with_gps())
        assert _gps_of(data)[0]
        out = sanitize_image(data)
        assert _gps_of(out)[0] == {}
        assert b"SecretCam" not in out
        a = Image.open(io.BytesIO(data)).convert("RGB").tobytes()
        assert a == Image.open(io.BytesIO(out)).convert("RGB").tobytes()

    def test_tiff_metadata_dropped_date_kept(self):
        data = _tiff()
        assert b"SecretCam" in data
        out = sanitize_image(data)
        assert b"SecretCam" not in out and b"home at" not in out
        ex = _gps_of(out)[1]
        assert ex.get(274) is None and ex.get(306) == "2025:12:24 14:30:45"
        assert Image.open(io.BytesIO(out)).convert("RGB").tobytes() == Image.open(io.BytesIO(data)).convert("RGB").tobytes()

    @pytest.mark.parametrize("fmt", ["GIF", "BMP"])
    def test_gif_bmp_pass(self, fmt):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, fmt)
        Image.open(io.BytesIO(sanitize_image(buf.getvalue()))).load()


class TestUndecodable:
    def test_truncated_jpeg(self):
        data = _jpeg(size=(400, 300))
        with pytest.raises(ImageUndecodable):
            sanitize_image(data[: len(data) // 2])

    def test_truncated_png(self):
        data = _png()
        with pytest.raises(ImageUndecodable):
            sanitize_image(data[: len(data) - 40])

    @pytest.mark.parametrize("data", [
        b"\xff\xd8\xff" + b"\x00" * 100, b"\x89PNG\r\n\x1a\n", b"\x00" * 64, b"GIF89a" + b"\x00" * 10,
    ])
    def test_signature_only_or_zeroed(self, data):
        with pytest.raises(ImageUndecodable):
            sanitize_image(data)


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    user = make_user(db, role=UserRole.admin, username="adm", email="a@example.com")
    prop = make_property(db)
    meter = make_meter(db, prop.id)
    return user, meter, tmp_path


_OCR = {"raw_texts": [], "detected_value": "123", "detected_serial": None, "detection_method": "ocr"}


class TestScanEndpoint:
    def _scan(self, client, user, data, **extra):
        with patch("app.routers.ocr._run_ocr_on_file", return_value=_OCR):
            return client.post("/api/ocr/scan", headers=auth_headers(user),
                               files={"file": ("m.jpg", data, "image/jpeg"), **extra})

    def test_truncated_image_rejected_nothing_stored(self, client, db, env):
        user, meter, up = env
        data = _jpeg(size=(400, 300))
        r = self._scan(client, user, data[: len(data) // 2])
        assert r.status_code == 400
        assert list(up.iterdir()) == []
        assert db.query(UploadFile).count() == 0

    def test_truncated_serial_image_rejected_nothing_stored(self, client, db, env):
        user, meter, up = env
        full = _jpeg(size=(400, 300))
        bad = full[: len(full) // 2]
        r = self._scan(client, user, _jpeg(), serial_file=("s.jpg", bad, "image/jpeg"))
        assert r.status_code == 400
        assert list(up.iterdir()) == []

    def test_stored_image_has_no_gps_and_hash_matches_file(self, client, db, env):
        user, meter, up = env
        r = self._scan(client, user, _jpeg(exif=_exif_with_gps()))
        assert r.status_code == 200
        stored = (up / Path(r.json()["image_path"]).name).read_bytes()
        assert _gps_of(stored)[0] == {}
        assert r.json()["image_hash"] == hashlib.sha256(stored).hexdigest()


class TestBulkScan:
    def _bulk(self, client, user, items):
        files = [("files", (n, d, "image/jpeg")) for n, d in items]
        with patch("app.routers.ocr._run_ocr_on_file", return_value=_OCR):
            r = client.post("/api/ocr/bulk-scan", headers=auth_headers(user), files=files)
        events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ") and not l.startswith("data: {\"meters")]
        return [e for e in events if "original_filename" in e]

    def test_truncated_flagged_not_stored_good_stripped(self, client, db, env):
        user, meter, up = env
        good = _jpeg(exif=_exif_with_gps())
        trunc = _jpeg(size=(400, 300))
        items = self._bulk(client, user, [("good.jpg", good), ("bad.jpg", trunc[: len(trunc) // 2])])
        by = {i["original_filename"]: i for i in items}
        assert by["bad.jpg"]["error"] and by["bad.jpg"]["temp_image_path"] is None
        assert by["good.jpg"]["error"] is None
        files = list(up.iterdir())
        assert len(files) == 1
        stored = files[0].read_bytes()
        assert _gps_of(stored)[0] == {}
        assert by["good.jpg"]["image_hash"] == hashlib.sha256(stored).hexdigest()
