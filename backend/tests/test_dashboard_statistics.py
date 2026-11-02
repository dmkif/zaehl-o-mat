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


# ── Oil range for a tank group ────────────────────────────────────────────────

class TestOilRangeGroup:
    def _tank(self, db, prop, name, levels):
        m = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name=name)
        now = NOW()
        for days_ago, level in levels:
            make_reading(db, m.id, level, now - timedelta(days=days_ago))
        return m

    def test_two_tanks_give_the_group_range(self, client, db):
        prop = make_property(db)
        # tank A: 300 L left, 1.5 L/day; tank B: 500 L left, 2.5 L/day
        self._tank(db, prop, "A", [(40, 360.0), (0, 300.0)])
        self._tank(db, prop, "B", [(40, 600.0), (0, 500.0)])
        body = _summary(client, _admin(db))
        assert body["oil_reichweite"][str(prop.id)] == 200

    def test_group_range_does_not_depend_on_tank_order(self, client, db):
        prop = make_property(db)
        self._tank(db, prop, "Z-last", [(40, 360.0), (0, 300.0)])
        self._tank(db, prop, "A-first", [(40, 600.0), (0, 500.0)])
        assert _summary(client, _admin(db))["oil_reichweite"][str(prop.id)] == 200

    def test_no_group_range_when_one_tank_has_no_rate_yet(self, client, db):
        prop = make_property(db)
        self._tank(db, prop, "A", [(40, 360.0), (0, 300.0)])
        self._tank(db, prop, "B", [(0, 500.0)])
        assert _summary(client, _admin(db))["oil_reichweite"][str(prop.id)] is None

    def test_30_day_range_present_when_every_tank_has_a_recent_rate(self, client, db):
        prop = make_property(db)
        # 100 days of 1 L/day, then 12 L/day over the last 20 days (300 L left in total)
        self._tank(db, prop, "A", [(100, 1000.0), (40, 900.0), (20, 660.0), (0, 420.0)])
        body = _summary(client, _admin(db))
        assert body["oil_reichweite_30d"][str(prop.id)] == 35  # 420 / 12
        # the yearly rate (~2.6 L/day) promises far more days than the winter pace
        assert body["oil_reichweite"][str(prop.id)] > body["oil_reichweite_30d"][str(prop.id)]

    def test_30_day_range_absent_without_recent_rate(self, client, db):
        prop = make_property(db)
        self._tank(db, prop, "A", [(120, 1000.0), (60, 900.0)])
        body = _summary(client, _admin(db))
        assert str(prop.id) not in body["oil_reichweite_30d"]

    def test_30_day_range_absent_when_recent_rate_is_zero(self, client, db):
        prop = make_property(db)
        self._tank(db, prop, "A", [(100, 1000.0), (40, 900.0), (20, 900.0), (0, 900.0)])
        body = _summary(client, _admin(db))
        assert str(prop.id) not in body["oil_reichweite_30d"]


# ── statistics block: per property and overall ────────────────────────────────

class TestSummaryStatistics:
    def _water(self, db, prop, per_day, name="W"):
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3, name=name)
        now = NOW()
        make_reading(db, m.id, 0.0, now - timedelta(days=60))
        make_reading(db, m.id, per_day * 60, now)
        return m

    def test_per_property_and_overall_figures(self, client, db):
        a = make_property(db, "Haus")
        b = make_property(db, "Wohnung")
        self._water(db, a, 0.2)
        self._water(db, b, 0.6)
        stats = _summary(client, _admin(db))["statistics"]
        assert stats["by_property"][str(a.id)]["name"] == "Haus"
        assert stats["by_property"][str(a.id)]["types"]["water"]["yearly"] == pytest.approx(0.2, abs=0.001)
        assert stats["by_property"][str(b.id)]["types"]["water"]["yearly"] == pytest.approx(0.6, abs=0.001)
        water = stats["overall"]["water"]
        assert water["yearly"] == pytest.approx(0.8, abs=0.002)
        assert water["properties_used"] == 2
        assert water["mean_per_property"]["yearly"] == pytest.approx(0.4, abs=0.002)

    def test_overall_equals_sum_of_per_property_figures(self, client, db):
        props = [make_property(db, f"P{i}") for i in range(3)]
        for i, p in enumerate(props):
            self._water(db, p, 0.1 * (i + 1))
        stats = _summary(client, _admin(db))["statistics"]
        per = [stats["by_property"][str(p.id)]["types"]["water"]["yearly"] for p in props]
        assert stats["overall"]["water"]["yearly"] == pytest.approx(sum(per), abs=0.001)

    def test_member_of_one_property_gets_no_overall_and_no_foreign_data(self, client, db):
        mine = make_property(db, "Mine")
        other = make_property(db, "Other")
        self._water(db, mine, 0.2)
        self._water(db, other, 5.0)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=mine.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        stats = _summary(client, user)["statistics"]
        assert stats["overall"] is None
        assert list(stats["by_property"]) == [str(mine.id)]
        assert "Other" not in str(stats)

    def test_overall_of_a_member_of_two_of_three_properties_excludes_the_third(self, client, db):
        a, b, c = (make_property(db, n) for n in "ABC")
        self._water(db, a, 0.2)
        self._water(db, b, 0.4)
        self._water(db, c, 10.0)
        user = make_user(db, role=UserRole.user)
        for p in (a, b):
            db.add(PropertyUser(property_id=p.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        water = _summary(client, user)["statistics"]["overall"]["water"]
        assert water["yearly"] == pytest.approx(0.6, abs=0.002)
        assert water["properties_used"] == 2

    def test_user_without_properties_gets_empty_statistics(self, client, db):
        make_property(db)
        user = make_user(db, role=UserRole.user)
        stats = _summary(client, user)["statistics"]
        assert stats["by_property"] == {}
        assert stats["overall"] is None

    def test_two_tanks_add_up_within_a_property(self, client, db):
        a = make_property(db, "A")
        b = make_property(db, "B")
        for name in ("T1", "T2"):
            t = make_meter(db, a.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name=name)
            make_reading(db, t.id, 600.0, NOW() - timedelta(days=40))
            make_reading(db, t.id, 500.0, NOW())
        t = make_meter(db, b.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T3")
        make_reading(db, t.id, 400.0, NOW() - timedelta(days=40))
        make_reading(db, t.id, 300.0, NOW())
        stats = _summary(client, _admin(db))["statistics"]
        assert stats["by_property"][str(a.id)]["types"]["oil"]["yearly"] == pytest.approx(5.0, abs=0.01)
        assert stats["by_property"][str(a.id)]["types"]["oil"]["meter_count"] == 2
        assert stats["overall"]["oil"]["yearly"] == pytest.approx(7.5, abs=0.01)

    def test_property_without_data_is_not_counted_in_the_mean(self, client, db):
        a, b, c = (make_property(db, n) for n in "ABC")
        self._water(db, a, 0.2)
        self._water(db, b, 0.6)
        make_meter(db, c.id, meter_type=MeterType.water, unit=MeterUnit.m3, name="empty")
        water = _summary(client, _admin(db))["statistics"]["overall"]["water"]
        assert water["properties_used"] == 2
        assert water["mean_per_property"]["yearly"] == pytest.approx(0.4, abs=0.002)


# ── GET /api/dashboard/yearly and cost ────────────────────────────────────────

from zoneinfo import ZoneInfo  # noqa: E402

from app.models import OilDelivery, PriceEntry  # noqa: E402

FIXED_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def lt(y, m, d):
    """Local (Europe/Berlin) midnight as aware UTC."""
    return datetime(y, m, d, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr("app.routers.dashboard._now", lambda: FIXED_NOW)
    return FIXED_NOW


class TestYearlyEndpoint:
    def _water(self, db, prop, name="Wasser"):
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3, name=name)
        for d, v in ((lt(2025, 1, 1), 0.0), (lt(2025, 10, 15), 100.0), (lt(2026, 1, 1), 130.0), (lt(2026, 10, 15), 430.0)):
            make_reading(db, m.id, v, d)
        return m

    def test_unauthenticated_returns_401(self, client, db):
        assert client.get("/api/dashboard/yearly").status_code == 401

    def test_year_to_date_against_same_period_last_year(self, client, db, fixed_now):
        prop = make_property(db)
        m = self._water(db, prop)
        body = client.get(f"/api/dashboard/yearly?meter_id={m.id}", headers=auth_headers(_admin(db))).json()
        s = body["series"][0]
        assert body["scope"]["kind"] == "meter"
        assert s["meter_type"] == "water" and s["unit"] == "m³"
        assert s["year_to_date"] == pytest.approx(300.0, abs=0.5)
        assert s["previous_year_same_period"] == pytest.approx(100.0, abs=0.5)
        assert s["delta_pct"] == pytest.approx(200.0, abs=2.0)

    def test_cost_from_price_entry(self, client, db, fixed_now):
        prop = make_property(db)
        m = self._water(db, prop)
        db.add(PriceEntry(meter_id=m.id, price_per_unit=0.5, valid_from=lt(2025, 1, 1)))
        db.commit()
        s = client.get(f"/api/dashboard/yearly?meter_id={m.id}", headers=auth_headers(_admin(db))).json()["series"][0]
        assert s["cost"] == pytest.approx(150.0, abs=0.2)
        assert s["cost_complete"] is True

    def test_no_price_means_no_cost_but_consumption_stays(self, client, db, fixed_now):
        prop = make_property(db)
        m = self._water(db, prop)
        s = client.get(f"/api/dashboard/yearly?meter_id={m.id}", headers=auth_headers(_admin(db))).json()["series"][0]
        assert s["cost"] is None
        assert s["cost_complete"] is False
        assert s["year_to_date"] == pytest.approx(300.0, abs=0.5)

    def test_oil_cost_is_the_money_spent_on_deliveries(self, client, db, fixed_now):
        prop = make_property(db)
        t1 = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T1")
        t2 = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T2")
        for t in (t1, t2):
            make_reading(db, t.id, 1000.0, lt(2026, 1, 1))
            make_reading(db, t.id, 800.0, lt(2026, 10, 1))
        db.add(OilDelivery(property_id=prop.id, liters=1000, total_price=1200.0, delivered_at=lt(2026, 3, 1)))
        db.add(OilDelivery(property_id=prop.id, liters=500, price_per_liter=1.0, delivered_at=lt(2025, 3, 1)))  # last year
        db.commit()
        s = client.get(f"/api/dashboard/yearly?property_id={prop.id}", headers=auth_headers(_admin(db))).json()["series"][0]
        assert s["meter_type"] == "oil"
        # two tanks: the delivery belongs to the property and is counted once
        assert s["cost"] == pytest.approx(1200.0)
        assert s["cost_complete"] is True

    def test_unpriced_oil_delivery_is_incomplete(self, client, db, fixed_now):
        prop = make_property(db)
        t = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T")
        make_reading(db, t.id, 1000.0, lt(2026, 1, 1))
        make_reading(db, t.id, 800.0, lt(2026, 10, 1))
        db.add(OilDelivery(property_id=prop.id, liters=1000, total_price=1200.0, delivered_at=lt(2026, 3, 1)))
        db.add(OilDelivery(property_id=prop.id, liters=500, delivered_at=lt(2026, 5, 1)))
        db.commit()
        s = client.get(f"/api/dashboard/yearly?property_id={prop.id}", headers=auth_headers(_admin(db))).json()["series"][0]
        assert s["cost"] == pytest.approx(1200.0)
        assert s["cost_complete"] is False

    def test_all_scope_only_accessible_properties(self, client, db, fixed_now):
        mine, other = make_property(db, "Mine"), make_property(db, "Other")
        self._water(db, mine)
        self._water(db, other)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=mine.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        mine_only = client.get("/api/dashboard/yearly", headers=auth_headers(user)).json()["series"][0]
        both = client.get("/api/dashboard/yearly", headers=auth_headers(_admin(db))).json()["series"][0]
        assert both["year_to_date"] == pytest.approx(2 * mine_only["year_to_date"], abs=0.5)

    def test_foreign_scope_is_403_and_bad_input_is_422(self, client, db, fixed_now):
        prop = make_property(db)
        m = self._water(db, prop)
        user = make_user(db, role=UserRole.user)
        h = auth_headers(user)
        assert client.get(f"/api/dashboard/yearly?meter_id={m.id}", headers=h).status_code == 403
        assert client.get(f"/api/dashboard/yearly?property_id={prop.id}", headers=h).status_code == 403
        assert client.get(f"/api/dashboard/yearly?meter_id={m.id}&property_id={prop.id}", headers=h).status_code == 422
        assert client.get("/api/dashboard/yearly?meter_id=x", headers=h).status_code == 422

    def test_no_data_gives_null_not_zero(self, client, db, fixed_now):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        s = client.get(f"/api/dashboard/yearly?meter_id={m.id}", headers=auth_headers(_admin(db))).json()["series"][0]
        assert s["year_to_date"] is None and s["previous_year_same_period"] is None and s["delta_pct"] is None


class TestMonthlyCost:
    def test_water_month_cost_is_value_times_price(self, client, db, fixed_now):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3)
        make_reading(db, m.id, 0.0, lt(2026, 8, 1))
        make_reading(db, m.id, 62.0, lt(2026, 10, 1))
        db.add(PriceEntry(meter_id=m.id, price_per_unit=2.0, valid_from=lt(2026, 1, 1)))
        db.commit()
        body = client.get(f"/api/dashboard/monthly?meter_id={m.id}&months=3", headers=auth_headers(_admin(db))).json()
        by_month = {r["month"]: r for r in body["series"][0]["months"]}
        assert by_month["2026-08"]["cost"] == pytest.approx(62 * 31 / 61 * 2.0, abs=0.05)
        assert by_month["2026-10"]["cost"] is None  # no value yet

    def test_month_cost_null_without_price(self, client, db, fixed_now):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3)
        make_reading(db, m.id, 0.0, lt(2026, 8, 1))
        make_reading(db, m.id, 62.0, lt(2026, 10, 1))
        body = client.get(f"/api/dashboard/monthly?meter_id={m.id}&months=3", headers=auth_headers(_admin(db))).json()
        assert all(r["cost"] is None for r in body["series"][0]["months"])

    def test_oil_month_cost_is_the_spend_of_that_month(self, client, db, fixed_now):
        prop = make_property(db)
        t = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T")
        make_reading(db, t.id, 1000.0, lt(2026, 8, 1))
        make_reading(db, t.id, 900.0, lt(2026, 10, 1))
        db.add(OilDelivery(property_id=prop.id, liters=800, total_price=900.0, delivered_at=lt(2026, 9, 10)))
        db.commit()
        body = client.get(f"/api/dashboard/monthly?property_id={prop.id}&months=3", headers=auth_headers(_admin(db))).json()
        by_month = {r["month"]: r for r in body["series"][0]["months"]}
        assert by_month["2026-09"]["cost"] == pytest.approx(900.0)
        assert by_month["2026-08"]["cost"] is None


class TestUnusuallyHighFlag:
    def test_water_meter_with_doubled_pace_is_flagged(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3)
        now = NOW()
        make_reading(db, m.id, 0.0, now - timedelta(days=150))
        make_reading(db, m.id, 60.0, now - timedelta(days=60))    # 0.67/day
        make_reading(db, m.id, 180.0, now - timedelta(days=20))   # 3/day
        make_reading(db, m.id, 300.0, now)                         # 6/day
        e = _summary(client, _admin(db))["meters"][0]
        assert e["unusually_high"] is True

    def test_same_pattern_on_an_oil_tank_is_not_flagged(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank")
        now = NOW()
        for days_ago, level in ((150, 1000.0), (60, 940.0), (20, 820.0), (0, 700.0)):
            make_reading(db, m.id, level, now - timedelta(days=days_ago))
        e = _summary(client, _admin(db))["meters"][0]
        assert e["unusually_high"] is False

    def test_young_meter_is_not_flagged(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id, meter_type=MeterType.water, unit=MeterUnit.m3)
        now = NOW()
        make_reading(db, m.id, 0.0, now - timedelta(days=30))
        make_reading(db, m.id, 1.0, now - timedelta(days=20))
        make_reading(db, m.id, 100.0, now)
        assert _summary(client, _admin(db))["meters"][0]["unusually_high"] is False

    def test_flag_is_false_when_there_is_nothing_to_compare(self, client, db):
        prop = make_property(db)
        m = make_meter(db, prop.id)
        make_reading(db, m.id, 5.0)
        assert _summary(client, _admin(db))["meters"][0]["unusually_high"] is False
