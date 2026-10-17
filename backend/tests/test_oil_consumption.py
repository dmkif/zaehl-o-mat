"""
Pure calculation tests for app.services.oil_consumption (no database).

Rules under test (specs/005-oil-deliveries/research.md R2-R5):
  - per-interval balance = level before - level after + delivered litres
  - rate = net balance of known intervals / their days, clamped >= 0
  - a rise above the jump threshold without a delivery is a probable delivery
    and the interval counts as unknown (no litres, no days)
"""
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.oil_consumption import compute_oil_consumption

UTC = timezone.utc

# The operator's real tank readings (litres) in 2026.
REAL_READINGS = [
    (1374, datetime(2026, 4, 2, 13, 51, tzinfo=UTC)),
    (1353, datetime(2026, 4, 3, 18, 25, tzinfo=UTC)),
    (1365, datetime(2026, 4, 4, 22, 13, tzinfo=UTC)),
    (1289, datetime(2026, 4, 16, 19, 36, tzinfo=UTC)),
    (1207, datetime(2026, 4, 26, 12, 16, tzinfo=UTC)),
    (1074, datetime(2026, 5, 23, 13, 23, tzinfo=UTC)),
    (945, datetime(2026, 7, 16, 18, 40, tzinfo=UTC)),
    (816, datetime(2026, 9, 13, 11, 27, tzinfo=UTC)),
]
REAL_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def readings(*points, start_id=1):
    """points: (litres, datetime) pairs -> reading-like objects with ascending ids."""
    return [
        SimpleNamespace(id=start_id + i, value=v, read_at=t)
        for i, (v, t) in enumerate(points)
    ]


def days(n, hours=0):
    return T0 + timedelta(days=n, hours=hours)


def delivery(liters, at):
    return SimpleNamespace(liters=liters, delivered_at=at)


def calc(rs, deliveries=(), dismissed=frozenset(), now=None):
    return compute_oil_consumption(rs, list(deliveries), set(dismissed), now or days(60))


class TestRealData:
    def test_eight_real_readings_give_about_3_4_liters_per_day(self):
        result = calc(readings(*REAL_READINGS), now=REAL_NOW)
        assert 3.2 <= result.liters_per_day <= 3.6
        assert result.probable == []

    def test_noise_reading_does_not_inflate_the_rate(self):
        # Without the +12 L reading (1353 -> 1365) the rate must be practically the same.
        without = [p for p in REAL_READINGS if p[0] != 1365]
        with_noise = calc(readings(*REAL_READINGS), now=REAL_NOW).liters_per_day
        clean = calc(readings(*without), now=REAL_NOW).liters_per_day
        assert with_noise == pytest.approx(clean, rel=0.01)

    def test_old_ten_liters_per_day_defect_is_gone(self):
        assert calc(readings(*REAL_READINGS), now=REAL_NOW).liters_per_day < 5


class TestEdgeCases:
    def test_no_readings_gives_none(self):
        assert calc([]).liters_per_day is None

    def test_single_reading_gives_none(self):
        assert calc(readings((1000, days(0)))).liters_per_day is None

    def test_identical_timestamps_contribute_no_days(self):
        result = calc(readings((1000, days(0)), (990, days(0))))
        assert result.liters_per_day is None
        assert result.known_days == 0

    def test_identical_timestamps_do_not_disturb_other_intervals(self):
        result = calc(readings((1000, days(0)), (995, days(0)), (900, days(10))))
        # sorted by (time, id): 1000 -> 995 same instant is skipped, 995 -> 900 over 10 days
        assert result.liters_per_day == pytest.approx(9.5)

    def test_unsorted_input_is_sorted_by_time(self):
        rs = readings((900, days(10)), (1000, days(0)), (950, days(5)))
        assert calc(rs).liters_per_day == pytest.approx(10.0)

    def test_readings_older_than_365_days_are_ignored(self):
        now = days(500)
        old = (2000, now - timedelta(days=400))
        old2 = (1500, now - timedelta(days=380))
        recent_a = (1000, now - timedelta(days=30))
        recent_b = (970, now)
        result = calc(readings(old, old2, recent_a, recent_b), now=now)
        assert result.liters_per_day == pytest.approx(1.0)

    def test_only_old_readings_give_none(self):
        now = days(500)
        rs = readings((2000, now - timedelta(days=400)), (1900, now - timedelta(days=399)))
        assert calc(rs, now=now).liters_per_day is None

    def test_only_noise_gives_zero_rate(self):
        # level wobbles but ends higher: net balance negative -> clamped to 0
        rs = readings((1000, days(0)), (1005, days(5)), (1010, days(10)))
        result = calc(rs)
        assert result.liters_per_day == 0
        assert result.probable == []

    def test_naive_datetimes_are_treated_as_utc(self):
        rs = readings((1000, datetime(2026, 6, 1, 12)), (900, datetime(2026, 6, 11, 12)))
        assert calc(rs).liters_per_day == pytest.approx(10.0)

    def test_consumed_liters_is_net_total_of_known_intervals(self):
        rs = readings((1000, days(0)), (900, days(10)))
        assert calc(rs).consumed_liters == pytest.approx(100.0)


# ── US2: recorded deliveries ──────────────────────────────────────────────────

class TestRecordedDeliveries:
    def test_delivery_between_700_and_1600_gives_100_consumed(self):
        rs = readings((700, days(0)), (1600, days(10)))
        result = calc(rs, [delivery(1000, days(5))])
        assert result.consumed_liters == pytest.approx(100.0)
        assert result.liters_per_day == pytest.approx(10.0)
        assert result.probable == []

    def test_delivery_equal_to_rise_gives_zero_never_negative(self):
        rs = readings((700, days(0)), (1600, days(10)))
        result = calc(rs, [delivery(900, days(5))])
        assert result.consumed_liters == 0
        assert result.liters_per_day == 0

    def test_delivery_smaller_than_rise_is_clamped_at_zero(self):
        # 700 - 1600 + 400 = -500: a negative balance with a recorded delivery is 0
        rs = readings((700, days(0)), (1600, days(10)))
        assert calc(rs, [delivery(400, days(5))]).consumed_liters == 0

    def test_delivery_larger_than_rise_leaves_the_difference_as_consumption(self):
        rs = readings((700, days(0)), (1600, days(10)))
        assert calc(rs, [delivery(1200, days(5))]).consumed_liters == pytest.approx(300.0)

    def test_clamp_applies_per_interval_not_to_the_sum(self):
        # interval 1: 700 - 1000 + 100 = -200 -> 0, interval 2 consumes 50
        rs = readings((700, days(0)), (1000, days(10)), (950, days(20)))
        result = calc(rs, [delivery(100, days(5))])
        assert result.consumed_liters == pytest.approx(50.0)

    def test_several_deliveries_each_in_their_own_interval(self):
        rs = readings((500, days(0)), (1400, days(10)), (1300, days(20)), (2100, days(30)))
        ds = [delivery(1000, days(5)), delivery(900, days(25))]
        result = calc(rs, ds)
        # (500-1400+1000) + (1400-1300) + (1300-2100+900) = 100 + 100 + 100
        assert result.consumed_liters == pytest.approx(300.0)

    def test_delivery_exactly_at_reading_time_counts_once_in_ending_interval(self):
        rs = readings((700, days(0)), (1600, days(10)), (1590, days(20)))
        result = calc(rs, [delivery(1000, days(10))])
        assert result.consumed_liters == pytest.approx(100.0 + 10.0)

    def test_delivery_at_first_reading_time_is_ignored(self):
        rs = readings((700, days(0)), (690, days(10)))
        assert calc(rs, [delivery(1000, days(0))]).consumed_liters == pytest.approx(10.0)

    def test_delivery_before_first_and_after_last_reading_is_ignored(self):
        rs = readings((700, days(0)), (690, days(10)))
        ds = [delivery(1000, days(-5)), delivery(1000, days(15))]
        assert calc(rs, ds).consumed_liters == pytest.approx(10.0)

    def test_two_deliveries_in_one_interval_are_summed(self):
        rs = readings((200, days(0)), (1500, days(10)))
        ds = [delivery(700, days(3)), delivery(700, days(6))]
        assert calc(rs, ds).consumed_liters == pytest.approx(100.0)

    def test_delivery_with_naive_datetime(self):
        rs = readings((700, days(0)), (1600, days(10)))
        d = delivery(1000, days(5).replace(tzinfo=None))
        assert calc(rs, [d]).consumed_liters == pytest.approx(100.0)


class TestAssignDeliveriesToMeters:
    def test_delivery_goes_to_meter_with_larger_rise(self):
        from app.services.oil_consumption import assign_deliveries

        tank_a = readings((700, days(0)), (1600, days(10)), start_id=1)
        tank_b = readings((500, days(0)), (520, days(10)), start_id=10)
        d = delivery(900, days(5))
        assigned = assign_deliveries({"a": tank_a, "b": tank_b}, [d])
        assert assigned["a"] == [d]
        assert assigned["b"] == []

    def test_tie_goes_to_first_meter(self):
        from app.services.oil_consumption import assign_deliveries

        a = readings((700, days(0)), (800, days(10)), start_id=1)
        b = readings((700, days(0)), (800, days(10)), start_id=10)
        d = delivery(100, days(5))
        assigned = assign_deliveries({"a": a, "b": b}, [d])
        assert assigned["a"] == [d] and assigned["b"] == []

    def test_delivery_outside_all_intervals_is_dropped(self):
        from app.services.oil_consumption import assign_deliveries

        a = readings((700, days(0)), (800, days(10)))
        assert assign_deliveries({"a": a}, [delivery(100, days(20))]) == {"a": []}


# ── US3: probable deliveries ──────────────────────────────────────────────────

class TestProbableDeliveries:
    def test_jump_without_delivery_is_unknown_and_probable(self):
        rs = readings((700, days(0)), (1600, days(10)))
        result = calc(rs)
        assert result.liters_per_day is None  # no known days at all
        assert result.known_days == 0
        assert len(result.probable) == 1
        p = result.probable[0]
        assert (p.from_reading_id, p.to_reading_id) == (1, 2)
        assert p.estimated_liters == 900
        assert p.from_at == days(0) and p.to_at == days(10)

    def test_jump_does_not_affect_the_average_of_other_intervals(self):
        rs = readings((1000, days(0)), (900, days(10)), (1800, days(20)), (1700, days(30)))
        result = calc(rs)
        assert result.liters_per_day == pytest.approx(10.0)
        assert result.known_days == pytest.approx(20.0)
        assert [(p.from_reading_id, p.to_reading_id) for p in result.probable] == [(2, 3)]

    def test_estimated_liters_rounded_to_whole_liters(self):
        rs = readings((700.0, days(0)), (1300.6, days(10)))
        assert calc(rs).probable[0].estimated_liters == 601

    def test_rise_just_below_threshold_is_absorbed_as_noise(self):
        # highest level 1000 L -> threshold max(50, 50) = 50 L; rise of exactly 50 is not above
        rs = readings((1000, days(0)), (950, days(5)), (1000, days(10)))
        result = calc(rs)
        assert result.probable == []
        assert result.known_days == pytest.approx(10.0)

    def test_rise_just_above_threshold_is_probable(self):
        rs = readings((1000, days(0)), (950, days(5)), (1001, days(10)))
        assert len(calc(rs).probable) == 1

    def test_dismissed_pair_stays_excluded_and_is_not_listed(self):
        rs = readings((1000, days(0)), (900, days(10)), (1800, days(20)), (1700, days(30)))
        result = calc(rs, dismissed={(2, 3)})
        assert result.probable == []
        assert result.known_days == pytest.approx(20.0)
        assert [i.state for i in result.intervals] == ["known", "dismissed_jump", "known"]

    def test_dismissal_of_other_pair_does_not_hide_this_one(self):
        rs = readings((700, days(0)), (1600, days(10)))
        assert len(calc(rs, dismissed={(7, 8)}).probable) == 1

    def test_threshold_scales_with_tank_size(self):
        small = readings((200, days(0)), (140, days(5)), (200, days(10)))  # rise 60 > max(50, 10)
        assert len(calc(small).probable) == 1
        large = readings((2000, days(0)), (1900, days(5)), (1990, days(10)))  # rise 90 < max(50, 100)
        assert calc(large).probable == []

    def test_recorded_delivery_removes_the_probable_entry(self):
        rs = readings((700, days(0)), (1600, days(10)))
        result = calc(rs, [delivery(1000, days(5))])
        assert result.probable == []
        assert result.consumed_liters == pytest.approx(100.0)

    def test_two_jumps_are_both_listed_in_order(self):
        rs = readings((500, days(0)), (1500, days(10)), (1400, days(15)), (2400, days(30)))
        assert [(p.from_reading_id, p.to_reading_id) for p in calc(rs).probable] == [(1, 2), (3, 4)]


# ── US4: noise robustness ─────────────────────────────────────────────────────

class TestNoiseRobustness:
    def test_four_readings_within_ten_percent_of_first_last_value(self):
        rs = readings((1000, days(0)), (995, days(1)), (1003, days(2)), (990, days(4)))
        expected = (1000 - 990) / 4
        assert calc(rs).liters_per_day == pytest.approx(expected, rel=0.10)

    def test_dense_noisy_sampling_vs_two_readings_differ_by_at_most_error_over_period(self):
        rng = random.Random(42)
        true_rate, period, noise = 5.0, 60, 8.0
        level = lambda d: 2000 - true_rate * d
        dense = [(level(d) + rng.uniform(-noise, noise), days(d)) for d in range(0, period + 1, 2)]
        two = [dense[0], dense[-1]]
        r_dense = calc(readings(*dense)).liters_per_day
        r_two = calc(readings(*two)).liters_per_day
        # both are first-last telescoping: they are identical, and within noise/period of the truth
        assert r_dense == pytest.approx(r_two, abs=1e-6)
        assert abs(r_dense - true_rate) <= 2 * noise / period + 1e-6

    @pytest.mark.parametrize("seed", range(10))
    def test_telescoping_property(self, seed):
        rng = random.Random(seed)
        n = rng.randint(3, 25)
        t, level, pts = 0.0, 3000.0, []
        for _ in range(n):
            pts.append((level, days(0) + timedelta(days=t)))
            t += rng.uniform(0.2, 6)
            # every step stays below the jump threshold (150 L for a 3000 L tank)
            level += rng.uniform(-40, 25)
        result = calc(readings(*pts), now=days(0) + timedelta(days=t + 1))
        assert result.probable == []
        span = (pts[-1][1] - pts[0][1]).total_seconds() / 86400
        expected = max(0.0, (pts[0][0] - pts[-1][0]) / span)
        assert result.liters_per_day == pytest.approx(expected, abs=1e-3)
