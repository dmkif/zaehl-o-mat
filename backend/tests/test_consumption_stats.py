"""
Pure tests for app.services.consumption_stats (feature 015, no database).

Rules under test: specs/015-consumption-statistics/{spec,research,data-model}.md
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.consumption_stats import intervals_from_readings, yearly_rate

UTC = timezone.utc
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def at(days, hours=0):
    return T0 + timedelta(days=days, hours=hours)


def rs(*points, start_id=1):
    """points: (value, datetime) -> reading-like objects with ascending ids."""
    return [SimpleNamespace(id=start_id + i, value=v, read_at=t) for i, (v, t) in enumerate(points)]


class TestIntervalsFromReadings:
    def test_positive_delta_is_known_consumption(self):
        ivs = intervals_from_readings(rs((0, at(0)), (300, at(30))))
        assert len(ivs) == 1
        assert ivs[0].consumption == 300
        assert ivs[0].days == pytest.approx(30)
        assert ivs[0].state == "known"

    def test_negative_delta_is_unknown(self):
        ivs = intervals_from_readings(rs((300, at(0)), (280, at(10))))
        assert ivs[0].consumption is None
        assert ivs[0].state == "skipped"

    def test_zero_delta_is_unknown_like_the_existing_average(self):
        # the existing yearly average ignores an unchanged reading pair (neither litres nor days)
        ivs = intervals_from_readings(rs((100, at(0)), (100, at(10))))
        assert ivs[0].consumption is None

    def test_identical_timestamps_are_dropped(self):
        assert intervals_from_readings(rs((0, at(0)), (5, at(0)))) == []

    def test_fewer_than_two_readings(self):
        assert intervals_from_readings([]) == []
        assert intervals_from_readings(rs((1, at(0)))) == []

    def test_input_order_does_not_matter(self):
        a = intervals_from_readings(rs((0, at(0)), (10, at(5)), (30, at(10))))
        b = intervals_from_readings(list(reversed(rs((0, at(0)), (10, at(5)), (30, at(10))))))
        assert [i.consumption for i in a] == [i.consumption for i in b] == [10, 20]

    def test_naive_datetimes_are_treated_as_utc(self):
        naive = [SimpleNamespace(id=1, value=0, read_at=datetime(2026, 6, 1)),
                 SimpleNamespace(id=2, value=10, read_at=datetime(2026, 6, 3))]
        assert intervals_from_readings(naive)[0].days == pytest.approx(2)


class TestYearlyRate:
    def test_matches_the_old_helper_semantics(self):
        # 0 -> 300 (30 d), 300 -> 280 skipped, 280 -> 380 (10 d): 400 / 40
        ivs = intervals_from_readings(rs((0, at(0)), (300, at(30)), (280, at(40)), (380, at(50))))
        assert yearly_rate(ivs) == pytest.approx(10.0)

    def test_none_without_known_days(self):
        assert yearly_rate([]) is None
        assert yearly_rate(intervals_from_readings(rs((5, at(0)), (5, at(3))))) is None


class TestTankGroup:
    """All active oil tanks of a property are one group (FR-024)."""

    def test_group_key_is_the_property(self):
        from app.services.consumption_stats import tank_group_key

        pid = object()
        assert tank_group_key(SimpleNamespace(property_id=pid)) is pid

    def test_sum_rates_adds_tanks(self):
        from app.services.consumption_stats import sum_rates

        assert sum_rates([1.5, 2.5]) == pytest.approx(4.0)

    def test_sum_rates_one_tank_unchanged(self):
        from app.services.consumption_stats import sum_rates

        assert sum_rates([3.4]) == 3.4

    def test_sum_rates_none_when_no_tank_has_a_rate(self):
        from app.services.consumption_stats import sum_rates

        assert sum_rates([None, None]) is None
        assert sum_rates([]) is None

    def test_sum_rates_skips_tanks_without_rate(self):
        from app.services.consumption_stats import sum_rates

        assert sum_rates([2.0, None]) == 2.0
