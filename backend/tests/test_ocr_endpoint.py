"""
Tests for /api/ocr/scan and /api/ocr/bulk-scan

All OCR / LLM calls are mocked so the test suite runs without GPU hardware,
EasyOCR model weights, or a live Ollama instance.

Covers:
  POST /api/ocr/scan
  - unauthenticated → 401
  - invalid content-type (not image/*) → 400
  - valid JPEG but unsupported magic bytes (fake content-type) → 400
  - file exceeds 10 MB → 413
  - engine=llm without OLLAMA_URL configured → 503
  - happy path: valid JPEG, mocked OCR → 200 with image_path and detected_value
  - meter_id not found → 404
  - meter_id found but user lacks access → 403
  - meter_id found, admin → 200

  POST /api/ocr/bulk-scan (SSE)
  - unauthenticated → 401
  - no files in form → 400
  - more than 50 files → 400
  - SSE stream: one data event per image, final complete event
  - oversized file in batch appears as error item in stream
  - invalid MIME in batch appears as error item in stream

  Unit-level:
  - _fix_seven_segment: known substitutions
  - _normalize_serial: whitespace/dash/dot/slash stripped and uppercased
  - _match_serial_to_meters: exact, partial, none, ambiguous
"""
import io
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import MeterType, MeterUnit, PropertyUser, PropertyUserRole, UserRole
from app.routers.ocr import (
    _fix_seven_segment,
    _format_hint_text,
    _match_serial_to_meters,
    _matches_format,
    _normalize_serial,
)
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user

# ── Minimal valid JPEG (1×1 pixel) ────────────────────────────────────────────
_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'"
    b"9=82<.342\x1edL\t\x10\x17\x19\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e"
    b"\x1e\x1e\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
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

_MOCK_OCR_RESULT = {
    "raw_texts": [{"text": "12345", "conf": 0.95}],
    "detected_value": "12345",
    "detected_serial": None,
}

# Used to patch _run_ocr_on_file to avoid GPU / disk / EasyOCR
_OCR_PATCH = "app.routers.ocr._run_ocr_on_file"


def _make_file_tuple(
    name: str = "meter.jpg",
    data: bytes = _JPEG,
    mime: str = "image/jpeg",
):
    return ("files", (name, io.BytesIO(data), mime))


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/ocr/scan  —  single-image
# ──────────────────────────────────────────────────────────────────────────────

class TestOcrScanEndpoint:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.post("/api/ocr/scan", files={"file": ("m.jpg", _JPEG, "image/jpeg")})
        assert r.status_code == 401

    def test_non_image_content_type_rejected(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        r = client.post(
            "/api/ocr/scan",
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
            headers=auth_headers(user),
        )
        assert r.status_code == 400

    def test_wrong_magic_bytes_rejected(self, client, db, tmp_path, monkeypatch):
        """Content-Type says image/jpeg but bytes are plaintext — filetype check must catch this."""
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        r = client.post(
            "/api/ocr/scan",
            files={"file": ("bad.jpg", b"this is not a jpeg", "image/jpeg")},
            headers=auth_headers(user),
        )
        assert r.status_code == 400

    def test_oversized_file_rejected_with_413(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        big = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 + 1)  # >10 MB with JPEG magic
        r = client.post(
            "/api/ocr/scan",
            files={"file": ("big.jpg", big, "image/jpeg")},
            headers=auth_headers(user),
        )
        assert r.status_code == 413

    def test_engine_llm_without_ollama_url_returns_503(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        monkeypatch.delenv("OLLAMA_URL", raising=False)
        user = make_user(db)
        r = client.post(
            "/api/ocr/scan",
            data={"engine": "llm"},
            files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
            headers=auth_headers(user),
        )
        assert r.status_code == 503

    def test_valid_jpeg_returns_detected_value(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/scan",
                data={"engine": "ocr"},
                files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
                headers=auth_headers(user),
            )
        assert r.status_code == 200
        body = r.json()
        assert body["detected_value"] == "12345"
        assert body["image_path"].startswith("uploads/")

    def test_meter_id_not_found_returns_404(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        admin = make_user(db, role=UserRole.admin)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/scan",
                data={"engine": "ocr", "meter_id": str(uuid.uuid4())},
                files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
                headers=auth_headers(admin),
            )
        assert r.status_code == 404

    def test_inaccessible_meter_returns_403(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user, username="stranger", email="stranger@x.com")
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/scan",
                data={"engine": "ocr", "meter_id": str(meter.id)},
                files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
                headers=auth_headers(user),
            )
        assert r.status_code == 403

    def test_admin_can_scan_any_meter(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/scan",
                data={"engine": "ocr", "meter_id": str(meter.id)},
                files={"file": ("meter.jpg", _JPEG, "image/jpeg")},
                headers=auth_headers(admin),
            )
        assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/ocr/bulk-scan  —  SSE batch
# ──────────────────────────────────────────────────────────────────────────────

def _parse_sse(raw: bytes) -> list[dict]:
    """Parse a raw SSE response body into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.decode().splitlines():
        if line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[5:].strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


class TestBulkScanEndpoint:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.post("/api/ocr/bulk-scan", files=[_make_file_tuple()])
        assert r.status_code == 401

    def test_no_files_returns_400(self, client, db):
        """Sending an empty files list should be rejected before OCR starts."""
        user = make_user(db)
        # FastAPI requires at least one File() value; send the form key with no files
        r = client.post(
            "/api/ocr/bulk-scan",
            headers=auth_headers(user),
            data={"engine": "ocr"},
        )
        # Either 400 (our explicit check) or 422 (FastAPI validation) is acceptable
        assert r.status_code in (400, 422)

    def test_more_than_50_files_returns_400(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        files = [_make_file_tuple(f"img{i}.jpg") for i in range(51)]
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/bulk-scan",
                files=files,
                data={"engine": "ocr"},
                headers=auth_headers(user),
            )
        assert r.status_code == 400

    def test_sse_content_type(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/bulk-scan",
                files=[_make_file_tuple()],
                data={"engine": "ocr"},
                headers=auth_headers(user),
            )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")

    def test_sse_stream_one_event_per_image_plus_complete(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        files = [_make_file_tuple(f"m{i}.jpg") for i in range(3)]
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/bulk-scan",
                files=files,
                data={"engine": "ocr"},
                headers=auth_headers(user),
            )
        assert r.status_code == 200
        events = _parse_sse(r.content)

        # 3 image events (unnamed = default) + 1 complete event
        image_events = [e for e in events if e.get("event") != "complete"]
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(image_events) == 3
        assert len(complete_events) == 1

    def test_sse_image_event_has_expected_fields(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/bulk-scan",
                files=[_make_file_tuple("meter.jpg")],
                data={"engine": "ocr"},
                headers=auth_headers(user),
            )
        events = _parse_sse(r.content)
        img_event = next(e for e in events if e.get("event") != "complete")
        data = img_event["data"]
        assert data["original_filename"] == "meter.jpg"
        assert data["detected_value"] == "12345"
        assert data["temp_image_path"].startswith("uploads/")
        assert "error" in data

    def test_sse_complete_event_contains_meters_list(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        prop = make_property(db)
        make_meter(db, prop.id, name="Z1")
        admin = make_user(db, role=UserRole.admin)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=_MOCK_OCR_RESULT):
            r = client.post(
                "/api/ocr/bulk-scan",
                files=[_make_file_tuple()],
                data={"engine": "ocr"},
                headers=auth_headers(admin),
            )
        events = _parse_sse(r.content)
        complete = next(e for e in events if e.get("event") == "complete")
        assert isinstance(complete["data"]["meters"], list)
        assert any(m["name"] == "Z1" for m in complete["data"]["meters"])

    def test_oversized_file_in_batch_yields_error_event(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        big_data = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 + 1)
        files = [("files", ("big.jpg", io.BytesIO(big_data), "image/jpeg"))]
        r = client.post(
            "/api/ocr/bulk-scan",
            files=files,
            data={"engine": "ocr"},
            headers=auth_headers(user),
        )
        assert r.status_code == 200
        events = _parse_sse(r.content)
        img_event = next(e for e in events if e.get("event") != "complete")
        assert img_event["data"]["error"] is not None

    def test_invalid_mime_in_batch_yields_error_event(self, client, db, monkeypatch, tmp_path):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        user = make_user(db)
        files = [("files", ("doc.pdf", io.BytesIO(b"%PDF-1.4 plaintext"), "image/jpeg"))]
        r = client.post(
            "/api/ocr/bulk-scan",
            files=files,
            data={"engine": "ocr"},
            headers=auth_headers(user),
        )
        assert r.status_code == 200
        events = _parse_sse(r.content)
        img_event = next(e for e in events if e.get("event") != "complete")
        assert img_event["data"]["error"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests — helper functions (no HTTP / no GPU)
# ──────────────────────────────────────────────────────────────────────────────

class TestFixSevenSegment:
    # _SEG7_SUBS maps: J→0  O→0  D→0  I→1  l→1  B→8  G→6  T→7
    @pytest.mark.parametrize("raw,expected", [
        ("J309735", "0309735"),       # J → 0
        ("0305 735", "0305735"),      # intra-digit space between digits stripped
        ("OOI", "001"),               # O→0, O→0, I→1
        ("BBBGGT", "888667"),         # B→8, B→8, B→8, G→6, G→6, T→7
        ("1234.5", "1234.5"),         # dot preserved (decimal reading)
        ("no digits", "no digits"),   # non-mapped chars pass through unchanged
    ])
    def test_substitutions(self, raw, expected):
        assert _fix_seven_segment(raw) == expected


class TestNormalizeSerial:
    @pytest.mark.parametrize("raw,expected", [
        ("123 456", "123456"),
        ("12-34-56", "123456"),
        ("12.34.56", "123456"),
        ("12/34/56", "123456"),
        ("abc DEF", "ABCDEF"),
        ("  AZ-09/02.03  ", "AZ090203"),
    ])
    def test_normalization(self, raw, expected):
        assert _normalize_serial(raw) == expected


class TestMatchSerialToMeters:
    def _fake_meter(self, serial: str, id_: str | None = None):
        m = MagicMock()
        m.id = id_ or str(uuid.uuid4())
        m.serial_number = serial
        return m

    def test_exact_match_returns_exact_confidence(self):
        m = self._fake_meter("ABC123")
        best, conf, candidates = _match_serial_to_meters("ABC123", [m])
        assert conf == "exact"
        assert best is m
        assert candidates == []

    def test_partial_match_returns_partial_confidence(self):
        m = self._fake_meter("XYZABC123XYZ")
        best, conf, _ = _match_serial_to_meters("ABC123", [m])
        assert conf == "partial"
        assert best is m

    def test_no_match_returns_none(self):
        m = self._fake_meter("999888")
        best, conf, candidates = _match_serial_to_meters("ABC123", [m])
        assert conf == "none"
        assert best is None

    def test_empty_serial_returns_none(self):
        m = self._fake_meter("ABC123")
        best, conf, _ = _match_serial_to_meters("", [m])
        assert conf == "none"
        assert best is None

    def test_meter_without_serial_number_skipped(self):
        m = self._fake_meter("")
        best, conf, _ = _match_serial_to_meters("ABC123", [m])
        assert conf == "none"

    def test_multiple_exact_matches_returns_partial_confidence(self):
        """Ambiguous exact matches → confidence is downgraded to partial."""
        m1 = self._fake_meter("ABC123")
        m2 = self._fake_meter("ABC123")
        _, conf, _ = _match_serial_to_meters("ABC123", [m1, m2])
        assert conf == "partial"

    def test_normalization_applied_before_matching(self):
        """Serial with dashes/spaces should still match a clean stored serial."""
        m = self._fake_meter("ABC123")
        best, conf, _ = _match_serial_to_meters("ABC-123", [m])
        assert conf == "exact"
        assert best is m


# ── _matches_format ────────────────────────────────────────────────────────────

class TestMatchesFormat:
    """Unit tests for _matches_format — validates decimal digit count per meter type."""

    @pytest.mark.parametrize("value,meter_type,expected", [
        # electricity: exactly 1 decimal digit
        ("12345678.9",  "electricity", True),
        ("12345678",    "electricity", False),   # no decimal → missing
        ("1234567.89",  "electricity", False),   # 2 decimal digits → wrong
        ("12345678.0",  "electricity", True),    # zero digit still counts as 1 decimal
        # water: exactly 3 decimal digits
        ("123.456",     "water", True),
        ("123",         "water", False),         # no decimal
        ("123.4",       "water", False),         # 1 decimal only
        ("123.4567",    "water", False),         # 4 decimals
        # oil: exactly 0 decimal digits (integer value)
        ("1234",        "oil", True),
        ("12.34",       "oil", False),           # unexpected decimal
        # German comma notation treated as decimal
        ("12345678,9",  "electricity", True),
        ("123,456",     "water", True),
        ("1234,0",      "oil", False),           # comma with digit counts as decimal
        # Missing info → always True (cannot validate)
        (None,          "water", True),
        ("123.456",     None,    True),
        ("123.456",     "unknown_type", True),
    ])
    def test_matches_format(self, value, meter_type, expected):
        assert _matches_format(value, meter_type) == expected


# ── _format_hint_text ──────────────────────────────────────────────────────────

class TestFormatHintText:
    """Unit tests for _format_hint_text — builds LLM CONTEXT block."""

    def test_electricity_german_comma_notation(self):
        text = _format_hint_text(12345678.9, "electricity")
        assert "12345678,9" in text
        assert "kWh" in text

    def test_electricity_shows_pattern_and_digit_count(self):
        text = _format_hint_text(12345678.9, "electricity")
        assert "########,#" in text
        assert "1" in text  # "The rightmost 1 digit(s)..."
        assert "comma" in text.lower()

    def test_water_german_comma_notation(self):
        text = _format_hint_text(123.456, "water")
        assert "123,456" in text
        assert "m³" in text

    def test_water_shows_pattern_and_digit_count(self):
        text = _format_hint_text(123.456, "water")
        assert "###,###" in text
        assert "3" in text  # "The rightmost 3 digit(s)..."

    def test_oil_no_decimal_separator(self):
        text = _format_hint_text(1234.0, "oil")
        assert "1234" in text
        assert "L" in text
        assert "no decimal" in text.lower()

    def test_ge_constraint_always_present(self):
        for meter_type in ("electricity", "water", "oil"):
            text = _format_hint_text(100.0, meter_type)
            assert "≥" in text or ">=" in text

    def test_context_header_present(self):
        text = _format_hint_text(100.0, "water")
        assert "CONTEXT" in text

    def test_unknown_meter_type_returns_string(self):
        text = _format_hint_text(100.0, None)
        assert isinstance(text, str)
        # Displays value without specific unit/pattern info
        assert "100" in text

    def test_electricity_high_precision_rounded_to_one_decimal(self):
        text = _format_hint_text(12345678.12345, "electricity")
        # Should show exactly 1 decimal digit for electricity
        assert "12345678,1" in text


# ── _run_ocr_on_file hint-retry ───────────────────────────────────────────────

class TestHintRetryRun:
    """
    Integration tests for the hint-retry logic inside _run_ocr_on_file.

    _llm_fallback is mocked so no GPU or Ollama is needed.
    asyncio.run() is used to drive the coroutine from sync test code.
    """

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_format_mismatch_triggers_second_llm_call(self, tmp_path):
        """When the first-pass value has the wrong decimal count, LLM is called again with hint."""
        filepath = tmp_path / "meter.jpg"
        filepath.write_bytes(_JPEG)

        calls = []

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            calls.append({"last_reading": last_reading, "meter_type": meter_type})
            if last_reading is None:
                return ("123456", None)   # wrong format for water (no decimal)
            return ("123.456", None)      # correct on retry

        with (
            patch("app.routers.ocr._llm_fallback", mock_llm),
            patch("app.routers.ocr.settings.ollama_url", "http://ollama:11434"),
        ):
            from app.routers.ocr import _run_ocr_on_file
            result = self._run(
                _run_ocr_on_file(filepath, engine="auto", last_reading=100.0, meter_type="water")
            )

        assert len(calls) == 2, "Expected exactly 2 LLM calls (first-pass + hint-retry)"
        assert calls[0]["last_reading"] is None    # first pass: no hint injected
        assert calls[1]["last_reading"] == 100.0   # retry: hint injected
        assert calls[1]["meter_type"] == "water"
        assert result["detected_value"] == "123.456"

    def test_correct_format_skips_retry(self, tmp_path):
        """When the first-pass value already has the right decimal count, no retry."""
        filepath = tmp_path / "meter.jpg"
        filepath.write_bytes(_JPEG)

        calls = []

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            calls.append(last_reading)
            return ("123.456", None)   # 3 decimals → correct for water

        with (
            patch("app.routers.ocr._llm_fallback", mock_llm),
            patch("app.routers.ocr.settings.ollama_url", "http://ollama:11434"),
        ):
            from app.routers.ocr import _run_ocr_on_file
            result = self._run(
                _run_ocr_on_file(filepath, engine="auto", last_reading=100.0, meter_type="water")
            )

        assert len(calls) == 1, "No retry expected when format already matches"
        assert result["detected_value"] == "123.456"

    def test_no_retry_without_context(self, tmp_path):
        """When last_reading is None, hint-retry must not be triggered regardless of format."""
        filepath = tmp_path / "meter.jpg"
        filepath.write_bytes(_JPEG)

        calls = []

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            calls.append(last_reading)
            return ("123456", None)   # wrong format for water, but no context → no retry

        with (
            patch("app.routers.ocr._llm_fallback", mock_llm),
            patch("app.routers.ocr.settings.ollama_url", "http://ollama:11434"),
        ):
            from app.routers.ocr import _run_ocr_on_file
            result = self._run(
                _run_ocr_on_file(filepath, engine="auto", last_reading=None, meter_type="water")
            )

        assert len(calls) == 1, "No retry without last_reading context"
        assert result["detected_value"] == "123456"

    def test_retry_keeps_first_value_when_second_returns_none(self, tmp_path):
        """When the retry LLM call returns None, the original first-pass value is preserved."""
        filepath = tmp_path / "meter.jpg"
        filepath.write_bytes(_JPEG)

        calls = []

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            calls.append(last_reading)
            if last_reading is None:
                return ("123456", None)   # wrong format → triggers retry
            return (None, None)           # retry also fails

        with (
            patch("app.routers.ocr._llm_fallback", mock_llm),
            patch("app.routers.ocr.settings.ollama_url", "http://ollama:11434"),
        ):
            from app.routers.ocr import _run_ocr_on_file
            result = self._run(
                _run_ocr_on_file(filepath, engine="auto", last_reading=100.0, meter_type="water")
            )

        assert len(calls) == 2
        assert result["detected_value"] == "123456", "First-pass value must survive failed retry"

    def test_llm_engine_also_retries_on_mismatch(self, tmp_path):
        """engine='llm' path also applies hint-retry logic."""
        filepath = tmp_path / "meter.jpg"
        filepath.write_bytes(_JPEG)

        calls = []

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            calls.append(last_reading)
            if last_reading is None:
                return ("12345678", None)   # missing decimal for electricity
            return ("12345678.9", None)

        with (
            patch("app.routers.ocr._llm_fallback", mock_llm),
            patch("app.routers.ocr.settings.ollama_url", "http://ollama:11434"),
        ):
            from app.routers.ocr import _run_ocr_on_file
            result = self._run(
                _run_ocr_on_file(
                    filepath, engine="llm",
                    last_reading=12345678.8, meter_type="electricity",
                )
            )

        assert len(calls) == 2
        assert result["detected_value"] == "12345678.9"


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/ocr/hint-rescan  —  manual meter selection hint-retry
# ──────────────────────────────────────────────────────────────────────────────

class TestHintRescanEndpoint:
    """HTTP integration tests for POST /api/ocr/hint-rescan."""

    def _post(self, client, headers, *, image_path, meter_id, current_value=None, engine="auto"):
        data = {"image_path": image_path, "meter_id": str(meter_id), "engine": engine}
        if current_value is not None:
            data["current_value"] = current_value
        return client.post("/api/ocr/hint-rescan", data=data, headers=headers)

    def test_format_already_ok_returns_unchanged_no_llm(self, client, db, tmp_path, monkeypatch):
        """When current_value already matches the meter format, return it without calling LLM."""
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        monkeypatch.setattr("app.routers.ocr.settings.ollama_url", "http://ollama:11434")

        img = tmp_path / "meter.jpg"
        img.write_bytes(_JPEG)

        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.electricity)
        user = make_user(db)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()

        llm_calls = []
        with patch("app.routers.ocr._llm_fallback", side_effect=lambda *a, **kw: llm_calls.append(1) or ("99999.9", None)):
            r = self._post(
                client,
                auth_headers(user),
                image_path=f"{img.name}",
                meter_id=meter.id,
                current_value="12345.6",   # 1 decimal → electricity OK
            )

        assert r.status_code == 200
        body = r.json()
        assert body["detected_value"] == "12345.6"
        assert body["hint_applied"] is False
        assert len(llm_calls) == 0

    def test_format_mismatch_triggers_llm_with_hint(self, client, db, tmp_path, monkeypatch):
        """When current_value has wrong decimal count, LLM is called and result is returned."""
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        monkeypatch.setattr("app.routers.ocr.settings.ollama_url", "http://ollama:11434")

        img = tmp_path / "meter.jpg"
        img.write_bytes(_JPEG)

        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.electricity)
        user = make_user(db)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()

        make_reading(db, meter.id, 10000.5)

        hint_args = {}

        def mock_llm(fp, already_cropped, last_reading=None, meter_type=None):
            hint_args.update(last_reading=last_reading, meter_type=meter_type)
            return ("12345.7", None)

        with patch("app.routers.ocr._llm_fallback", mock_llm):
            r = self._post(
                client,
                auth_headers(user),
                image_path=f"{img.name}",
                meter_id=meter.id,
                current_value="12345",   # missing decimal → electricity mismatch
            )

        assert r.status_code == 200
        body = r.json()
        assert body["detected_value"] == "12345.7"
        assert body["detection_method"] == "llm"
        assert body["hint_applied"] is True
        # Hint was passed with the last reading value
        assert hint_args["last_reading"] == pytest.approx(10000.5)
        assert hint_args["meter_type"] == "electricity"

    def test_engine_ocr_skips_llm_even_on_mismatch(self, client, db, tmp_path, monkeypatch):
        """With engine=ocr, no LLM call is made regardless of format mismatch."""
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))
        monkeypatch.setattr("app.routers.ocr.settings.ollama_url", "http://ollama:11434")

        img = tmp_path / "meter.jpg"
        img.write_bytes(_JPEG)

        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.electricity)
        user = make_user(db)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()

        llm_calls = []
        with patch("app.routers.ocr._llm_fallback", side_effect=lambda *a, **kw: llm_calls.append(1) or ("99999.9", None)):
            r = self._post(
                client,
                auth_headers(user),
                image_path=f"{img.name}",
                meter_id=meter.id,
                current_value="12345",   # mismatch but engine=ocr
                engine="ocr",
            )

        assert r.status_code == 200
        body = r.json()
        assert body["detected_value"] == "12345"
        assert body["hint_applied"] is False
        assert len(llm_calls) == 0

    def test_image_not_found_returns_404(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))

        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.water)
        user = make_user(db, role=UserRole.admin)
        db.commit()

        r = self._post(
            client,
            auth_headers(user),
            image_path="does-not-exist.jpg",
            meter_id=meter.id,
        )
        assert r.status_code == 404

    def test_meter_not_found_returns_404(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))

        img = tmp_path / "meter.jpg"
        img.write_bytes(_JPEG)

        user = make_user(db, role=UserRole.admin)
        db.commit()

        r = self._post(
            client,
            auth_headers(user),
            image_path=f"{img.name}",
            meter_id=uuid.uuid4(),  # non-existent
        )
        assert r.status_code == 404

    def test_unauthenticated_returns_401(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))

        img = tmp_path / "meter.jpg"
        img.write_bytes(_JPEG)

        r = client.post(
            "/api/ocr/hint-rescan",
            data={"image_path": img.name, "meter_id": str(uuid.uuid4())},
        )
        assert r.status_code == 401

    def test_path_traversal_rejected(self, client, db, tmp_path, monkeypatch):
        """A path_traversal attempt (e.g. ../../etc/passwd) must return 404."""
        monkeypatch.setattr("app.routers.ocr.settings.upload_path", str(tmp_path))

        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.water)
        user = make_user(db, role=UserRole.admin)
        db.commit()

        r = self._post(
            client,
            auth_headers(user),
            image_path="../../etc/passwd",
            meter_id=meter.id,
        )
        # The traversal must not succeed — any non-200 response is acceptable
        assert r.status_code in (403, 404)

