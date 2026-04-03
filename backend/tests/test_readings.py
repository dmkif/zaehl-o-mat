"""
Tests for /api/properties/{property_id}/meters/{meter_id}/readings

Covers:
  - List with from/to date filters
  - Pagination (limit / offset)
  - Create reading (manual source)
  - Delete reading
  - Access control for read vs. write
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import PropertyUser, PropertyUserRole, ReadingSource, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user


def _url(prop_id, meter_id):
    return f"/api/properties/{prop_id}/meters/{meter_id}/readings"


class TestListReadings:
    def test_unauthenticated_returns_401(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        r = client.get(_url(prop.id, meter.id))
        assert r.status_code == 401

    def test_admin_lists_all_readings(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 100.0, now - timedelta(hours=2))
        make_reading(db, meter.id, 110.0, now - timedelta(hours=1))
        make_reading(db, meter.id, 120.0, now)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(_url(prop.id, meter.id), headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_from_filter_excludes_older_readings(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 1.0, now - timedelta(days=10))
        make_reading(db, meter.id, 2.0, now - timedelta(days=1))
        make_reading(db, meter.id, 3.0, now)
        admin = make_user(db, role=UserRole.admin)
        from_dt = (now - timedelta(days=2)).isoformat()
        r = client.get(_url(prop.id, meter.id), params={"from": from_dt}, headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_to_filter_excludes_newer_readings(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 1.0, now - timedelta(days=5))
        make_reading(db, meter.id, 2.0, now - timedelta(days=1))
        make_reading(db, meter.id, 3.0, now)
        admin = make_user(db, role=UserRole.admin)
        to_dt = (now - timedelta(days=2)).isoformat()
        r = client.get(_url(prop.id, meter.id), params={"to": to_dt}, headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_limit_restricts_result_count(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        for i in range(5):
            make_reading(db, meter.id, float(i))
        admin = make_user(db, role=UserRole.admin)
        r = client.get(_url(prop.id, meter.id), params={"limit": 2}, headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_offset_skips_readings(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        for i in range(5):
            make_reading(db, meter.id, float(i), now - timedelta(hours=5 - i))
        admin = make_user(db, role=UserRole.admin)
        r_full = client.get(_url(prop.id, meter.id), headers=auth_headers(admin))
        r_offset = client.get(_url(prop.id, meter.id), params={"offset": 2}, headers=auth_headers(admin))
        assert len(r_offset.json()) == len(r_full.json()) - 2

    def test_unassigned_user_gets_403(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        r = client.get(_url(prop.id, meter.id), headers=auth_headers(user))
        assert r.status_code == 403

    def test_assigned_user_can_list_readings(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        make_reading(db, meter.id, 42.0)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.get(_url(prop.id, meter.id), headers=auth_headers(user))
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestCreateReading:
    def test_admin_can_create_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(admin),
            json={"value": 999.5, "source": "manual"},
        )
        assert r.status_code == 201
        body = r.json()
        assert float(body["value"]) == pytest.approx(999.5)
        assert body["source"] == "manual"

    def test_property_manager_can_create_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.manager))
        db.commit()
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(user),
            json={"value": 50.0, "source": "manual"},
        )
        assert r.status_code == 201

    def test_plain_property_user_cannot_create_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(user),
            json={"value": 50.0, "source": "manual"},
        )
        assert r.status_code == 403

    def test_reading_uses_current_time_when_omitted(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        before = datetime.now(timezone.utc)
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(admin),
            json={"value": 10.0},
        )
        after = datetime.now(timezone.utc)
        assert r.status_code == 201
        read_at_str = r.json()["read_at"]
        # SQLite may return naive datetime; strip tz for comparison
        read_at = datetime.fromisoformat(read_at_str).replace(tzinfo=None)
        before_naive = before.replace(tzinfo=None)
        after_naive = after.replace(tzinfo=None)
        assert before_naive <= read_at <= after_naive

    def test_missing_value_returns_422(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.post(_url(prop.id, meter.id), headers=auth_headers(admin), json={})
        assert r.status_code == 422

    def test_invalid_source_value_returns_422(self, client, db):
        """Regression: source='ocr' is not a valid ReadingSource value."""
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(admin),
            json={"value": 100.0, "source": "ocr"},
        )
        assert r.status_code == 422

    def test_create_reading_with_auto_source(self, client, db):
        """OCR-scanned readings must be saveable with source='auto'."""
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(admin),
            json={"value": 12345.6, "source": "auto", "image_path": "uploads/test.jpg"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["source"] == "auto"
        assert body["image_path"] == "uploads/test.jpg"

    def test_create_reading_trailing_slash_not_required(self, client, db):
        """POST without trailing slash must reach the endpoint (no redirect body loss)."""
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        # TestClient follows redirects by default, so this also validates the redirect works
        r = client.post(
            _url(prop.id, meter.id),
            headers=auth_headers(admin),
            json={"value": 777.0, "source": "manual"},
        )
        assert r.status_code == 201
        assert float(r.json()["value"]) == pytest.approx(777.0)

    def test_create_ocr_reading_full_flow(self, client, db):
        """Full OCR save flow: value as float, source auto, image_path optional note."""
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        payload = {
            "value": 99834.5,
            "source": "auto",
            "image_path": "uploads/meter_scan.jpg",
            "note": "OCR confidence 0.92",
        }
        r = client.post(_url(prop.id, meter.id), headers=auth_headers(admin), json=payload)
        assert r.status_code == 201
        body = r.json()
        assert float(body["value"]) == pytest.approx(99834.5)
        assert body["source"] == "auto"
        assert body["image_path"] == "uploads/meter_scan.jpg"
        assert body["note"] == "OCR confidence 0.92"
        assert body["read_at"] is not None


class TestDeleteReading:
    def test_admin_can_delete_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        reading = make_reading(db, meter.id, 100.0)
        admin = make_user(db, role=UserRole.admin)
        r = client.delete(
            f"{_url(prop.id, meter.id)}/{reading.id}",
            headers=auth_headers(admin),
        )
        assert r.status_code == 204

    def test_plain_user_cannot_delete_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        reading = make_reading(db, meter.id, 100.0)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.delete(
            f"{_url(prop.id, meter.id)}/{reading.id}",
            headers=auth_headers(user),
        )
        assert r.status_code == 403

    def test_nonexistent_reading_returns_404(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.delete(
            f"{_url(prop.id, meter.id)}/999999",
            headers=auth_headers(admin),
        )
        assert r.status_code == 404
