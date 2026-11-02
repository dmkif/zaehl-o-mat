"""
API tests for the consumption statistics (feature 015).

Summary fields per meter: 30-day rate, rate since the last reading, trend.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import MeterType, MeterUnit, PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _summary(client, user):
    r = client.get("/api/dashboard/summary", headers=auth_headers(user))
    assert r.status_code == 200
    return r.json()


def _admin(db):
    return make_user(db, role=UserRole.admin, username="stats-admin", email="stats-admin@example.com")


class TestSummaryRecentFields:
    def test_unauthenticated_returns_401(self, client, db):
        assert client.get("/api/dashboard/summary").status_code == 401

    def test_heating_season_water_meter(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3)
        now = NOW()
        make_reading(db, m.id, 0.0, now - timedelta(days=100))
        make_reading(db, m.id, 100.0, now - timedelta(days=40))   # 1.67/day all summer
        make_reading(db, m.id, 260.0, now - timedelta(days=20))   # 8/day since the heating runs
        make_reading(db, m.id, 420.0, now)                         # 8/day
        e = _summary(client, _admin(db))["meters"][0]
        assert e["avg_daily_consumption_365d"] == pytest.approx(4.2, abs=0.01)
        assert e["avg_daily_consumption_30d"] == pytest.approx(8.0, abs=0.01)
        assert e["daily_consumption_last_interval"] == pytest.approx(8.0, abs=0.01)
        assert e["trend_30d_vs_365d_pct"] == pytest.approx(90.5, abs=0.2)

    def test_oil_tank_recent_rate_uses_balance_rules(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank")
        now = NOW()
        for days_ago, level in ((100, 1000.0), (20, 900.0), (10, 820.0), (0, 740.0)):
            make_reading(db, m.id, level, now - timedelta(days=days_ago))
        e = _summary(client, _admin(db))["meters"][0]
        assert e["avg_daily_consumption_365d"] == pytest.approx(2.6, abs=0.01)
        # intervals ending in the last 30 days: 20->10 d, 10->0 d, and the 100->20 d one ends 20 d ago
        assert e["avg_daily_consumption_30d"] == pytest.approx(260 / 100, abs=0.01)
        assert e["daily_consumption_last_interval"] == pytest.approx(8.0, abs=0.01)
        assert e["trend_30d_vs_365d_pct"] == pytest.approx(0.0, abs=0.5)

    def test_single_reading_gives_null_everywhere(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        make_reading(db, m.id, 42.0)
        e = _summary(client, _admin(db))["meters"][0]
        assert e["avg_daily_consumption_30d"] is None
        assert e["daily_consumption_last_interval"] is None
        assert e["trend_30d_vs_365d_pct"] is None

    def test_last_reading_older_than_30_days_gives_no_recent_rate(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        now = NOW()
        make_reading(db, m.id, 0.0, now - timedelta(days=120))
        make_reading(db, m.id, 100.0, now - timedelta(days=60))
        e = _summary(client, _admin(db))["meters"][0]
        assert e["avg_daily_consumption_365d"] is not None
        assert e["avg_daily_consumption_30d"] is None
        assert e["trend_30d_vs_365d_pct"] is None

    def test_replaced_meter_is_excluded(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        m.replaced_at = NOW()
        db.commit()
        assert _summary(client, _admin(db))["meters"] == []

    def test_user_without_membership_sees_nothing(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        make_reading(db, m.id, 0.0, NOW() - timedelta(days=10))
        make_reading(db, m.id, 50.0, NOW())
        user = make_user(db, role=UserRole.user)
        body = _summary(client, user)
        assert body["meters"] == []

    def test_member_sees_own_property_only(self, client, db):
        mine = make_property(db, "Mine")
        other = make_property(db, "Other")
        m1 = make_meter(db, mine.id, name="A")
        m2 = make_meter(db, other.id, name="B")
        for m in (m1, m2):
            make_reading(db, m.id, 0.0, NOW() - timedelta(days=10))
            make_reading(db, m.id, 50.0, NOW())
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=mine.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        entries = _summary(client, user)["meters"]
        assert [e["meter_name"] for e in entries] == ["A"]


# ── GET /api/dashboard/monthly ────────────────────────────────────────────────

class TestMonthlyEndpoint:
    def _meter_with_year_of_readings(self, db, prop=None, name="Wasser"):
        prop = prop or make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3, name=name)
        now = NOW()
        for i in range(14, -1, -1):
            make_reading(db, m.id, float((14 - i) * 10), now - timedelta(days=30 * i))
        return prop, m

    def test_unauthenticated_returns_401(self, client, db):
        assert client.get("/api/dashboard/monthly").status_code == 401

    def test_meter_scope_returns_requested_months_oldest_first(self, client, db):
        _, m = self._meter_with_year_of_readings(db)
        admin = _admin(db)
        r = client.get(f"/api/dashboard/monthly?meter_id={m.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        body = r.json()
        assert body["scope"] == {"kind": "meter", "id": str(m.id)}
        assert len(body["series"]) == 1
        series = body["series"][0]
        assert series["meter_type"] == "water" and series["unit"] == "m³"
        months = series["months"]
        assert len(months) == 12
        assert [x["month"] for x in months] == sorted(x["month"] for x in months)
        assert months[-1]["state"] == "partial"
        assert months[0]["state"] in ("complete", "partial")

    def test_previous_year_is_attached_when_data_exists(self, client, db):
        _, m = self._meter_with_year_of_readings(db)
        admin = _admin(db)
        body = client.get(f"/api/dashboard/monthly?meter_id={m.id}&months=3", headers=auth_headers(admin)).json()
        assert any(x["previous_year"] is not None for x in body["series"][0]["months"])

    def test_months_bounds(self, client, db):
        admin = _admin(db)
        for bad in (0, 25, -1, "x"):
            r = client.get(f"/api/dashboard/monthly?months={bad}", headers=auth_headers(admin))
            assert r.status_code == 422, bad
        assert client.get("/api/dashboard/monthly?months=24", headers=auth_headers(admin)).status_code == 200

    def test_meter_and_property_together_is_422(self, client, db):
        prop, m = self._meter_with_year_of_readings(db)
        admin = _admin(db)
        r = client.get(f"/api/dashboard/monthly?meter_id={m.id}&property_id={prop.id}", headers=auth_headers(admin))
        assert r.status_code == 422

    def test_malformed_ids_are_422(self, client, db):
        admin = _admin(db)
        assert client.get("/api/dashboard/monthly?meter_id=nope", headers=auth_headers(admin)).status_code == 422
        assert client.get("/api/dashboard/monthly?property_id=nope", headers=auth_headers(admin)).status_code == 422

    def test_unknown_ids_are_404(self, client, db):
        import uuid
        admin = _admin(db)
        assert client.get(f"/api/dashboard/monthly?meter_id={uuid.uuid4()}", headers=auth_headers(admin)).status_code == 404
        assert client.get(f"/api/dashboard/monthly?property_id={uuid.uuid4()}", headers=auth_headers(admin)).status_code == 404

    def test_foreign_meter_and_property_are_403_without_data(self, client, db):
        prop, m = self._meter_with_year_of_readings(db)
        user = make_user(db, role=UserRole.user)
        h = auth_headers(user)
        r1 = client.get(f"/api/dashboard/monthly?meter_id={m.id}", headers=h)
        r2 = client.get(f"/api/dashboard/monthly?property_id={prop.id}", headers=h)
        assert r1.status_code == 403 and r2.status_code == 403
        assert "series" not in r1.json() and "series" not in r2.json()

    def test_member_with_read_only_role_can_read(self, client, db):
        prop, m = self._meter_with_year_of_readings(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.get(f"/api/dashboard/monthly?meter_id={m.id}", headers=auth_headers(user))
        assert r.status_code == 200

    def test_property_scope_groups_by_meter_type_and_sums_meters(self, client, db):
        prop, m1 = self._meter_with_year_of_readings(db, name="W1")
        _, m2 = self._meter_with_year_of_readings(db, prop=prop, name="W2")
        oil = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank")
        make_reading(db, oil.id, 1000.0, NOW() - timedelta(days=40))
        make_reading(db, oil.id, 900.0, NOW() - timedelta(days=1))
        admin = _admin(db)
        body = client.get(f"/api/dashboard/monthly?property_id={prop.id}&months=2", headers=auth_headers(admin)).json()
        assert body["scope"]["kind"] == "property"
        assert {s["meter_type"] for s in body["series"]} == {"water", "oil"}
        single = client.get(f"/api/dashboard/monthly?meter_id={m1.id}&months=2", headers=auth_headers(admin)).json()
        water = next(s for s in body["series"] if s["meter_type"] == "water")["months"]
        one = single["series"][0]["months"]
        assert water[-1]["value"] == pytest.approx(2 * one[-1]["value"], rel=1e-6)

    def test_all_scope_only_contains_accessible_properties(self, client, db):
        mine, _ = self._meter_with_year_of_readings(db)
        other, _ = self._meter_with_year_of_readings(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=mine.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        admin = _admin(db)
        all_admin = client.get("/api/dashboard/monthly?months=2", headers=auth_headers(admin)).json()
        all_user = client.get("/api/dashboard/monthly?months=2", headers=auth_headers(user)).json()
        assert all_user["scope"] == {"kind": "all", "id": None}
        v_user = all_user["series"][0]["months"][-1]["value"]
        v_admin = all_admin["series"][0]["months"][-1]["value"]
        assert v_admin == pytest.approx(2 * v_user, rel=1e-6)

    def test_replaced_meters_are_not_part_of_property_scope(self, client, db):
        prop, m = self._meter_with_year_of_readings(db)
        m.replaced_at = NOW()
        db.commit()
        admin = _admin(db)
        body = client.get(f"/api/dashboard/monthly?property_id={prop.id}", headers=auth_headers(admin)).json()
        assert body["series"] == []

    def test_user_without_membership_gets_empty_all_scope(self, client, db):
        self._meter_with_year_of_readings(db)
        user = make_user(db, role=UserRole.user)
        body = client.get("/api/dashboard/monthly", headers=auth_headers(user)).json()
        assert body["series"] == []
