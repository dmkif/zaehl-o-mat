"""
Tests for EXIF datetime extraction from images.

Creates test images at runtime with and without EXIF metadata to verify
that _extract_exif_datetime correctly reads DateTimeOriginal from the
Exif sub-IFD, falls back to DateTime in the root IFD, and returns None
for images without any EXIF date.
"""
import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from app.routers.ocr import _extract_exif_datetime


def _save_jpeg(img: Image.Image, exif_bytes: bytes | None = None) -> Path:
    """Save a PIL Image as a JPEG in a temp file and return the path."""
    buf = io.BytesIO()
    kwargs: dict = {"format": "JPEG"}
    if exif_bytes:
        kwargs["exif"] = exif_bytes
    img.save(buf, **kwargs)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(buf.getvalue())
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


class TestExtractExifDatetime:
    """Tests for _extract_exif_datetime helper."""

    def test_datetime_original_in_exif_ifd(self):
        """DateTimeOriginal (tag 36867) in Exif sub-IFD is extracted with date + time."""
        img = Image.new("RGB", (10, 10), color="red")
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[36867] = "2025:12:24 14:30:45"
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2025-12-24T14:30"
        finally:
            filepath.unlink(missing_ok=True)

    def test_datetime_digitized_in_exif_ifd(self):
        """DateTimeDigitized (tag 36868) is used when DateTimeOriginal is absent."""
        img = Image.new("RGB", (10, 10), color="green")
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[36868] = "2024:06:15 09:05:30"
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2024-06-15T09:05"
        finally:
            filepath.unlink(missing_ok=True)

    def test_datetime_original_takes_precedence(self):
        """DateTimeOriginal is preferred over DateTimeDigitized."""
        img = Image.new("RGB", (10, 10), color="blue")
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[36867] = "2025:01:01 00:00:00"
        exif_ifd[36868] = "2099:12:31 23:59:59"
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2025-01-01T00:00"
        finally:
            filepath.unlink(missing_ok=True)

    def test_fallback_to_root_datetime(self):
        """Tag 306 (DateTime) in root IFD is used when Exif sub-IFD has no date."""
        img = Image.new("RGB", (10, 10), color="yellow")
        exif = img.getexif()
        exif[306] = "2023:03:10 18:22:11"
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2023-03-10T18:22"
        finally:
            filepath.unlink(missing_ok=True)

    def test_no_exif_returns_none(self):
        """Image without any EXIF data returns None."""
        img = Image.new("RGB", (10, 10), color="white")
        filepath = _save_jpeg(img)
        try:
            result = _extract_exif_datetime(filepath)
            assert result is None
        finally:
            filepath.unlink(missing_ok=True)

    def test_exif_without_date_tags_returns_none(self):
        """Image with EXIF data but no date tags returns None."""
        img = Image.new("RGB", (10, 10), color="black")
        exif = img.getexif()
        exif[270] = "Test camera"  # ImageDescription — not a date tag
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result is None
        finally:
            filepath.unlink(missing_ok=True)

    def test_short_datetime_string_returns_date_only(self):
        """If EXIF DateTime is shorter than 19 chars, only the date part is returned."""
        img = Image.new("RGB", (10, 10), color="gray")
        exif = img.getexif()
        exif[306] = "2023:07:01"  # Only date, no time
        filepath = _save_jpeg(img, exif.tobytes())
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2023-07-01"
        finally:
            filepath.unlink(missing_ok=True)

    def test_nonexistent_file_returns_none(self):
        """Non-existent file path returns None (no exception raised)."""
        result = _extract_exif_datetime(Path("/tmp/does-not-exist-12345.jpg"))
        assert result is None

    def test_png_with_exif(self):
        """PNG images can also carry EXIF data."""
        img = Image.new("RGB", (10, 10), color="purple")
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[36867] = "2026:04:08 10:15:00"
        buf = io.BytesIO()
        img.save(buf, format="PNG", exif=exif.tobytes())
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(buf.getvalue())
        tmp.flush()
        tmp.close()
        filepath = Path(tmp.name)
        try:
            result = _extract_exif_datetime(filepath)
            assert result == "2026-04-08T10:15"
        finally:
            filepath.unlink(missing_ok=True)
