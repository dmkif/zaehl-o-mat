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


def ivs(*points):
    return intervals_from_readings(rs(*points))


class TestRecentRate:
    def test_heating_season_shows_in_30_day_rate(self):
        from app.services.consumption_stats import recent_rate, yearly_rate

        # 100 days at 1/day, then 20 days at 8/day
        intervals = ivs((0, at(0)), (100, at(100)), (260, at(120)))
        now = at(120)
        assert yearly_rate(intervals) == pytest.approx(260 / 120, rel=1e-3)
        # the 100-day interval ended 20 days ago -> inside the window and counts whole
        assert recent_rate(intervals, now) == pytest.approx(260 / 120, rel=1e-3)

    def test_only_intervals_ending_in_window(self):
        from app.services.consumption_stats import recent_rate

        intervals = ivs((0, at(0)), (100, at(100)), (260, at(120)), (420, at(140)))
        # window = days 110..140: only (120->140) ends after day 110 and (100->120) ends at 120
        # -> both count: 320 over 40 days
        assert recent_rate(intervals, at(140)) == pytest.approx(320 / 40)
        # window = days 120.. : only the last one counts: 160 / 20
        assert recent_rate(intervals, at(150)) == pytest.approx(8.0)

    def test_interval_crossing_window_start_counts_whole(self):
        from app.services.consumption_stats import recent_rate

        intervals = ivs((0, at(0)), (300, at(60)))
        assert recent_rate(intervals, at(80)) == pytest.approx(5.0)  # ends 20 days ago

    def test_none_when_last_reading_older_than_window(self):
        from app.services.consumption_stats import recent_rate

        intervals = ivs((0, at(0)), (300, at(60)))
        assert recent_rate(intervals, at(91)) is None

    def test_none_without_intervals(self):
        from app.services.consumption_stats import recent_rate

        assert recent_rate([], at(10)) is None

    def test_unknown_interval_is_skipped(self):
        from app.services.consumption_stats import recent_rate

        # decrease -> skipped; only the last known interval counts
        intervals = ivs((100, at(0)), (50, at(10)), (150, at(20)))
        assert recent_rate(intervals, at(20)) == pytest.approx(10.0)

    def test_oil_noise_is_absorbed_and_never_negative(self):
        from app.services.consumption_stats import recent_rate
        from app.services.oil_consumption import compute_oil_consumption

        # tank level 1000, +5 L reading error, then falling
        r = rs((1000, at(0)), (1005, at(5)), (990, at(10)))
        result = compute_oil_consumption(r, [], set(), at(10))
        assert recent_rate(result.intervals, at(10)) == pytest.approx(1.0)  # (1000-990)/10
        flat = compute_oil_consumption(rs((1000, at(0)), (1003, at(5))), [], set(), at(5))
        assert recent_rate(flat.intervals, at(5)) == 0.0  # net negative clamps to 0

    def test_oil_probable_delivery_does_not_count(self):
        from app.services.consumption_stats import recent_rate
        from app.services.oil_consumption import compute_oil_consumption

        r = rs((500, at(0)), (1500, at(10)))
        result = compute_oil_consumption(r, [], set(), at(10))
        assert recent_rate(result.intervals, at(10)) is None


class TestLastIntervalRate:
    def test_known(self):
        from app.services.consumption_stats import last_interval_rate

        assert last_interval_rate(ivs((0, at(0)), (10, at(5)), (50, at(15)))) == pytest.approx(4.0)

    def test_unknown_last_interval(self):
        from app.services.consumption_stats import last_interval_rate

        assert last_interval_rate(ivs((100, at(0)), (50, at(5)))) is None

    def test_oil_probable_delivery_last_interval_is_unknown(self):
        from app.services.consumption_stats import last_interval_rate
        from app.services.oil_consumption import compute_oil_consumption

        result = compute_oil_consumption(rs((500, at(0)), (1500, at(10))), [], set(), at(10))
        assert last_interval_rate(result.intervals) is None

    def test_none_without_intervals(self):
        from app.services.consumption_stats import last_interval_rate

        assert last_interval_rate([]) is None


class TestTrendPct:
    def test_signed_one_decimal(self):
        from app.services.consumption_stats import trend_pct

        assert trend_pct(12.0, 3.0) == 300.0
        assert trend_pct(2.0, 3.0) == -33.3
        assert trend_pct(3.0, 3.0) == 0.0

    def test_none_when_a_value_is_missing_or_yearly_not_positive(self):
        from app.services.consumption_stats import trend_pct

        assert trend_pct(None, 3.0) is None
        assert trend_pct(3.0, None) is None
        assert trend_pct(3.0, 0.0) is None


# ── Monthly view (US2) ────────────────────────────────────────────────────────

BERLIN = __import__("zoneinfo").ZoneInfo("Europe/Berlin")


def local(y, m, d, hh=0, mm=0):
    """Berlin wall-clock time as an aware UTC datetime."""
    return datetime(y, m, d, hh, mm, tzinfo=BERLIN).astimezone(UTC)


def months_by_key(rows):
    return {r.month: r for r in rows}


class TestMonthlySeries:
    def test_interval_is_split_proportionally_by_days(self):
        from app.services.consumption_stats import monthly_series

        # 1 Jan 00:00 -> 1 Mar 00:00 local (31 + 28 days), 590 units
        intervals = ivs((0, local(2026, 1, 1)), (590, local(2026, 3, 1)))
        rows = months_by_key(monthly_series(intervals, local(2026, 3, 1), months=3))
        assert rows["2026-01"].value == pytest.approx(590 * 31 / 59)
        assert rows["2026-02"].value == pytest.approx(590 * 28 / 59)
        assert rows["2026-01"].state == "complete"
        assert rows["2026-02"].state == "complete"

    def test_shares_add_up_to_the_interval_consumption(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 1, 17, 13, 5)), (777.7, local(2026, 4, 3, 7, 30)))
        rows = monthly_series(intervals, local(2026, 4, 3, 7, 30), months=4)
        assert sum(r.value or 0 for r in rows) == pytest.approx(777.7, abs=1e-6)

    def test_gap_month_is_no_data_not_zero(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 1, 1)), (30, local(2026, 2, 1)),
                        (30, local(2026, 4, 1)), (60, local(2026, 5, 1)))
        # Feb 1 -> Apr 1 reading unchanged -> skipped (unknown) -> February/March uncovered
        rows = months_by_key(monthly_series(intervals, local(2026, 5, 1), months=5))
        assert rows["2026-02"].state == "no_data" and rows["2026-02"].value is None
        assert rows["2026-03"].state == "no_data" and rows["2026-03"].value is None
        assert rows["2026-01"].value == pytest.approx(30)

    def test_current_month_is_partial(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 10, 1)), (50, local(2026, 10, 11)))
        rows = months_by_key(monthly_series(intervals, local(2026, 10, 11), months=2))
        assert rows["2026-10"].state == "partial"
        assert rows["2026-10"].value == pytest.approx(50)

    def test_always_exactly_months_entries_oldest_first(self):
        from app.services.consumption_stats import monthly_series

        rows = monthly_series([], local(2026, 11, 2), months=12)
        assert [r.month for r in rows][0] == "2025-12"
        assert [r.month for r in rows][-1] == "2026-11"
        assert len(rows) == 12
        assert all(r.state == "no_data" and r.value is None for r in rows)

    def test_long_interval_is_marked_estimated(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 1, 1)), (180, local(2026, 4, 1)))  # 90 days
        rows = months_by_key(monthly_series(intervals, local(2026, 4, 1), months=4))
        assert all(rows[m].estimated for m in ("2026-01", "2026-02", "2026-03"))

    def test_short_intervals_are_not_estimated(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 1, 1)), (10, local(2026, 1, 20)), (20, local(2026, 2, 1)))
        rows = months_by_key(monthly_series(intervals, local(2026, 2, 1), months=2))
        assert not rows["2026-01"].estimated

    def test_oil_unknown_interval_leaves_no_data(self):
        from app.services.consumption_stats import monthly_series
        from app.services.oil_consumption import compute_oil_consumption

        r = rs((500, local(2026, 1, 1)), (480, local(2026, 1, 11)), (1480, local(2026, 2, 1)))
        result = compute_oil_consumption(r, [], set(), local(2026, 2, 1))
        rows = months_by_key(monthly_series(result.intervals, local(2026, 2, 1), months=2))
        # only 1-11 Jan is known (20 L); the delivery interval is unknown
        assert rows["2026-01"].value == pytest.approx(20)
        assert rows["2026-01"].state == "partial"

    def test_berlin_month_boundary_summer_time(self):
        from app.services.consumption_stats import monthly_series

        # 23:30 UTC on 31 Jul is 01:30 on 1 Aug in Berlin (UTC+2)
        edge = datetime(2026, 7, 31, 23, 30, tzinfo=UTC)
        intervals = ivs((0, edge - timedelta(days=1)), (10, edge + timedelta(hours=1)))
        rows = months_by_key(monthly_series(intervals, edge + timedelta(hours=1), months=2))
        assert rows["2026-07"].value is not None and rows["2026-08"].value is not None
        # local midnight 1 Aug is 22:00 UTC; the interval ends 02:30 local: 2.5 h of 25 h
        assert rows["2026-08"].value == pytest.approx(10 * 2.5 / 25, rel=1e-6)

    def test_winter_time_boundary(self):
        from app.services.consumption_stats import monthly_series

        edge = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)  # 00:30 on 1 Jan in Berlin (UTC+1)
        intervals = ivs((0, edge - timedelta(hours=1)), (10, edge + timedelta(hours=1)))
        rows = months_by_key(monthly_series(intervals, edge + timedelta(hours=1), months=2))
        assert rows["2027-01"].value == pytest.approx(10 * 1.5 / 2, rel=1e-6)


class TestPreviousYear:
    def _two_years(self):
        # Jan 2025: 30 units, Jan 2026: 45 units, 1/day and 1.5/day
        return ivs(
            (0, local(2025, 1, 1)), (31, local(2025, 2, 1)),
            (31, local(2026, 1, 1)), (31 + 46.5, local(2026, 2, 1)),
        )

    def test_previous_year_and_percent(self):
        from app.services.consumption_stats import monthly_series

        rows = months_by_key(monthly_series(self._two_years(), local(2026, 2, 1), months=13))
        jan = rows["2026-01"]
        assert jan.value == pytest.approx(46.5)
        assert jan.previous_year == {"value": pytest.approx(31.0), "state": "complete"}
        assert jan.delta_pct == pytest.approx(50.0)

    def test_no_previous_year_means_none(self):
        from app.services.consumption_stats import monthly_series

        rows = months_by_key(monthly_series(self._two_years(), local(2026, 2, 1), months=3))
        assert rows["2025-12"].previous_year is None
        assert rows["2025-12"].delta_pct is None

    def test_percent_only_when_both_complete(self):
        from app.services.consumption_stats import monthly_series

        # current month partial, previous year complete
        intervals = ivs((0, local(2025, 10, 1)), (31, local(2025, 11, 1)),
                        (31, local(2026, 10, 1)), (56, local(2026, 10, 16)))
        rows = months_by_key(monthly_series(intervals, local(2026, 10, 16), months=1))
        row = rows["2026-10"]
        assert row.state == "partial"
        assert row.previous_year is not None
        assert row.delta_pct is None

    def test_no_percent_when_previous_is_zero(self):
        from app.services.consumption_stats import monthly_series

        intervals = ivs((0, local(2026, 1, 1)), (10, local(2026, 2, 1)))
        rows = monthly_series(intervals, local(2026, 2, 1), months=1)
        assert rows[0].delta_pct is None


class TestMergeMonthSeries:
    """Several meters of one type (a property, or all accessible properties) in one series."""

    def _series(self, *point_lists):
        from app.services.consumption_stats import monthly_series

        now = local(2026, 3, 1)
        return [monthly_series(ivs(*pts), now, months=3) for pts in point_lists]

    def test_values_add_up(self):
        from app.services.consumption_stats import merge_month_series

        a, b = self._series(
            [(0, local(2026, 1, 1)), (62, local(2026, 3, 1))],
            [(0, local(2026, 1, 1)), (31, local(2026, 3, 1))],
        )
        merged = months_by_key(merge_month_series([a, b]))
        assert merged["2026-01"].value == pytest.approx((62 + 31) * 31 / 59)
        assert merged["2026-01"].state == "complete"

    def test_state_is_complete_only_if_every_meter_with_data_is_complete(self):
        from app.services.consumption_stats import merge_month_series

        a, b = self._series(
            [(0, local(2026, 1, 1)), (62, local(2026, 3, 1))],
            [(0, local(2026, 1, 16)), (10, local(2026, 3, 1))],
        )
        merged = months_by_key(merge_month_series([a, b]))
        assert merged["2026-01"].state == "partial"

    def test_no_data_when_no_meter_has_data(self):
        from app.services.consumption_stats import merge_month_series

        (a,) = self._series([(0, local(2026, 2, 1)), (10, local(2026, 3, 1))])
        assert months_by_key(merge_month_series([a]))["2026-01"].value is None

    def test_single_series_is_unchanged(self):
        from app.services.consumption_stats import merge_month_series

        (a,) = self._series([(0, local(2026, 1, 1)), (62, local(2026, 3, 1))])
        assert [(m.month, m.value, m.state) for m in merge_month_series([a])] == [
            (m.month, m.value, m.state) for m in a
        ]

    def test_empty_input(self):
        from app.services.consumption_stats import merge_month_series

        assert merge_month_series([]) == []
