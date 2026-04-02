"""
Tests for /api/dashboard

Covers:
  - summary counts (properties, meters)
  - summary respects access control (user sees only own properties)
  - consumption endpoint: delta time-series, negative deltas skipped
  - consumption: 404 for unknown meter, 403 for inaccessible meter
  - cost-forecast: insufficient_data guard when <2 readings
  - cost-forecast: returns forecast array when data is present
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import MeterType, MeterUnit, OilMarketPrice, PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user


class TestDashboardSummary:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.get("/api/dashboard/summary")
        assert r.status_code == 401

    def test_summary_counts_are_correct(self, client, db):
        prop_a = make_property(db, "A")
        prop_b = make_property(db, "B")
        make_meter(db, prop_a.id, name="Z1")
        make_meter(db, prop_a.id, name="Z2")
        make_meter(db, prop_b.id, name="Z3")
        admin = make_user(db, role=UserRole.admin)
        r = client.get("/api/dashboard/summary", headers=auth_headers(admin))
        assert r.status_code == 200
        body = r.json()
        assert body["property_count"] == 2
        assert body["active_meter_count"] == 3

    def test_regular_user_sees_only_assigned_properties(self, client, db):
        prop_mine = make_property(db, "Mine")
        prop_other = make_property(db, "Other")
        make_meter(db, prop_mine.id)
        make_meter(db, prop_other.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop_mine.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.get("/api/dashboard/summary", headers=auth_headers(user))
        assert r.status_code == 200
        body = r.json()
        assert body["property_count"] == 1
        assert body["active_meter_count"] == 1

    def test_replaced_meters_not_counted_in_summary(self, client, db):
        prop = make_property(db)
        active = make_meter(db, prop.id, name="Aktiv")
        replaced = make_meter(db, prop.id, name="Ersetzt")
        replaced.replaced_at = datetime.now(timezone.utc)
        db.commit()
        admin = make_user(db, role=UserRole.admin)
        r = client.get("/api/dashboard/summary", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["active_meter_count"] == 1

    def test_latest_reading_appears_in_summary(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 50.0, now - timedelta(hours=1))
        make_reading(db, meter.id, 75.9, now)
        admin = make_user(db, role=UserRole.admin)
        r = client.get("/api/dashboard/summary", headers=auth_headers(admin))
        assert r.status_code == 200
        meter_data = r.json()["meters"][0]
        assert float(meter_data["latest_value"]) == pytest.approx(75.9)

    def test_oil_price_null_when_no_entry(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.get("/api/dashboard/summary", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["oil_market_price"]["price_per_100l"] is None

    def test_oil_price_present_when_entry_exists(self, client, db):
        import datetime as dt
        entry = OilMarketPrice(
            price_date=dt.date.today(),
            price_per_100l=Decimal("89.99"),
            source="heizoel-aktuell",
        )
        db.add(entry)
        db.commit()
        admin = make_user(db, role=UserRole.admin)
        r = client.get("/api/dashboard/summary", headers=auth_headers(admin))
        assert r.status_code == 200
        assert float(r.json()["oil_market_price"]["price_per_100l"]) == pytest.approx(89.99)


class TestConsumption:
    def test_unknown_meter_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/consumption/{uuid.uuid4()}", headers=auth_headers(admin))
        assert r.status_code == 404

    def test_inaccessible_meter_returns_403(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)  # not assigned to prop
        r = client.get(f"/api/dashboard/consumption/{meter.id}", headers=auth_headers(user))
        assert r.status_code == 403

    def test_consumption_delta_calculation(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 100.0, now - timedelta(hours=2))
        make_reading(db, meter.id, 115.0, now - timedelta(hours=1))
        make_reading(db, meter.id, 130.0, now)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/consumption/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 2
        assert float(data[0]["consumption"]) == pytest.approx(15.0)
        assert float(data[1]["consumption"]) == pytest.approx(15.0)

    def test_negative_deltas_are_skipped(self, client, db):
        """Meter rollover or correction should not produce negative consumption entries."""
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 100.0, now - timedelta(hours=2))
        make_reading(db, meter.id, 50.0, now - timedelta(hours=1))  # rollover / correction
        make_reading(db, meter.id, 60.0, now)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/consumption/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        data = r.json()["data"]
        # Only the +10 delta (50→60) should appear; the -50 delta is skipped
        assert len(data) == 1
        assert float(data[0]["consumption"]) == pytest.approx(10.0)

    def test_no_readings_returns_empty_data(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/consumption/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["data"] == []


class TestCostForecast:
    def test_insufficient_data_flag_when_single_reading(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        make_reading(db, meter.id, 100.0)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/cost-forecast/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["insufficient_data"] is True
        assert r.json()["forecast"] == []

    def test_forecast_returned_with_sufficient_data(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id, meter_type=MeterType.electricity, unit=MeterUnit.kwh)
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 0.0, now - timedelta(days=30))
        make_reading(db, meter.id, 300.0, now)  # 10 kWh/day
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/cost-forecast/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        body = r.json()
        assert body["insufficient_data"] is False
        assert len(body["forecast"]) > 0

    def test_unknown_meter_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/dashboard/cost-forecast/{uuid.uuid4()}", headers=auth_headers(admin))
        assert r.status_code == 404

    def test_inaccessible_meter_returns_403(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        r = client.get(f"/api/dashboard/cost-forecast/{meter.id}", headers=auth_headers(user))
        assert r.status_code == 403
