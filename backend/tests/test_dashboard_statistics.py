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
    return make_user(db, role=UserRole.admin)


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
