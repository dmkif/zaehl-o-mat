"""F-012 / F-013 / F-028: bulk-scan memory bounds, short DB sessions, no orphan files."""
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.models import MeterType, MeterUnit, PropertyUser, PropertyUserRole, UploadFile
from app.services import llm_client
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user
from tests.test_uploads import _MINIMAL_JPEG

_OCR_PATCH = "app.routers.ocr._run_ocr_on_file"
_OCR_RESULT = {"raw_texts": [], "detected_value": "1.0", "detected_serial": None, "detection_method": "ocr"}


def _img(i: int, pad: int = 0) -> bytes:
    return _MINIMAL_JPEG + bytes([i]) * pad


def _post(client, user, files, **data):
    return client.post(
        "/api/ocr/bulk-scan",
        files=[("files", (f"m{i}.jpg", blob, "image/jpeg")) for i, blob in enumerate(files)],
        data={"engine": "ocr", **data},
        headers=auth_headers(user),
    )


def _items(r):
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    return [e for e in events if "original_filename" in e]


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    return tmp_path


def _stored(p):
    return [f for f in p.iterdir() if f.is_file()]


# ── F-012 ─────────────────────────────────────────────────────────────────────

def test_total_budget_exceeded_is_413_without_ocr_or_files(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_total_bytes", 5000, raising=False)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)) as ocr:
        r = _post(client, user, [_img(i, 2500) for i in range(3)])
    assert r.status_code == 413
    ocr.assert_not_called()
    assert _stored(uploads) == []
    assert db.query(UploadFile).count() == 0


def test_within_budget_still_works(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_total_bytes", 50_000, raising=False)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = _post(client, user, [_img(i, 100) for i in range(3)])
    assert r.status_code == 200
    assert len(_items(r)) == 3


def test_file_count_setting_applies(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_files", 2, raising=False)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)) as ocr:
        r = _post(client, user, [_img(i) for i in range(3)])
    assert r.status_code == 400
    ocr.assert_not_called()


def test_oversized_file_is_item_error_and_not_stored(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_total_bytes", 50_000_000, raising=False)
    monkeypatch.setattr("app.routers.ocr._MAX_UPLOAD_BYTES", 2000)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = _post(client, user, [_img(1, 3000), _img(2, 100)])
    items = _items(r)
    assert r.status_code == 200
    assert "too large" in items[0]["error"]
    assert items[1]["error"] is None
    assert len(_stored(uploads)) == 1


def test_declared_content_length_over_route_limit_is_413(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_total_bytes", 1000, raising=False)
    monkeypatch.setattr("app.body_limit.MULTIPART_SLACK", 0, raising=False)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)) as ocr:
        r = _post(client, user, [_img(1, 3000)])
    assert r.status_code == 413
    ocr.assert_not_called()
    assert _stored(uploads) == []


def test_limit_does_not_touch_other_routes(client, db, uploads, monkeypatch):
    monkeypatch.setattr("app.config.settings.max_bulk_total_bytes", 10, raising=False)
    monkeypatch.setattr("app.body_limit.MULTIPART_SLACK", 0, raising=False)
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
        r = client.post("/api/ocr/scan", data={"engine": "ocr"},
                        files={"file": ("a.jpg", _img(1, 500), "image/jpeg")},
                        headers=auth_headers(user))
    assert r.status_code == 200


# ── F-013 ─────────────────────────────────────────────────────────────────────

def test_no_db_session_open_while_llm_runs(client, db, uploads, monkeypatch):
    user = make_user(db)
    prop = make_property(db)
    db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.manager))
    db.commit()
    meter = make_meter(db, prop.id, meter_type=MeterType.electricity, unit=MeterUnit.kwh)
    meter.serial_number = "SN123456"
    db.commit()
    make_reading(db, meter.id, 100.0)

    open_sessions = 0
    import app.routers.ocr as ocr_mod
    real = ocr_mod._short_session

    @contextmanager
    def tracked(bind):
        nonlocal open_sessions
        open_sessions += 1
        try:
            with real(bind) as s:
                yield s
        finally:
            open_sessions -= 1

    monkeypatch.setattr(ocr_mod, "_short_session", tracked)
    seen = {}

    async def fake_ocr(*a, **k):
        seen["ocr"] = (db.in_transaction(), open_sessions)
        return {"raw_texts": [], "detected_value": None, "detected_serial": "SN123456", "detection_method": "llm"}

    def fake_llm(*a, **k):
        seen["llm"] = (db.in_transaction(), open_sessions)
        return "12.3", None

    monkeypatch.setattr(ocr_mod, "llm_enabled", lambda: True)
    with patch(_OCR_PATCH, fake_ocr), patch("app.routers.ocr._llm_fallback", fake_llm):
        r = _post(client, user, [_img(1, 50)], engine="auto")
    assert r.status_code == 200
    item = _items(r)[0]
    assert item["matched_meter"]["serial_number"] == "SN123456"
    assert item["detected_value"] == "12.3"
    assert seen["ocr"] == (False, 0)
    assert seen["llm"] == (False, 0)
    assert db.query(UploadFile).count() == 1


# ── F-028 ─────────────────────────────────────────────────────────────────────

def test_scan_failure_leaves_no_file_or_registry_row(client, db, uploads):
    user = make_user(db)
    with patch("app.routers.ocr._llm_fallback", side_effect=llm_client.LocalModelUnavailable()), \
            patch("app.routers.ocr.llm_enabled", return_value=True):
        r = client.post("/api/ocr/scan", data={"engine": "llm"},
                        files={"file": ("a.jpg", _img(1, 50), "image/jpeg"),
                               "serial_file": ("s.jpg", _img(2, 50), "image/jpeg")},
                        headers=auth_headers(user))
    assert r.status_code == 503
    assert _stored(uploads) == []
    assert db.query(UploadFile).count() == 0


def test_scan_unexpected_failure_leaves_no_file(client, db, uploads):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            client.post("/api/ocr/scan", data={"engine": "ocr"},
                        files={"file": ("a.jpg", _img(1, 50), "image/jpeg")},
                        headers=auth_headers(user))
    assert _stored(uploads) == []
    assert db.query(UploadFile).count() == 0


def test_bulk_item_failure_leaves_no_file(client, db, uploads):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, side_effect=[RuntimeError("x"), dict(_OCR_RESULT)]):
        r = _post(client, user, [_img(1, 50), _img(2, 50)])
    items = _items(r)
    assert items[0]["error"] == "OCR processing failed"
    assert items[0]["temp_image_path"] is None
    assert items[1]["temp_image_path"] is not None
    assert len(_stored(uploads)) == 1
    assert db.query(UploadFile).count() == 1


def test_bulk_llm_error_leaves_no_file(client, db, uploads):
    user = make_user(db)
    with patch(_OCR_PATCH, new_callable=AsyncMock, side_effect=llm_client.LocalModelUnavailable()):
        r = _post(client, user, [_img(1, 50)])
    assert _items(r)[0]["temp_image_path"] is None
    assert _stored(uploads) == []
    assert db.query(UploadFile).count() == 0
