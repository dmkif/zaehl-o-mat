"""
Oil tank consumption from tank-level readings.

Pure functions on plain lists (no database), so tile, range ("Reichweite"),
cost forecast and the type aggregate all share one calculation.

Rules (specs/005-oil-deliveries/research.md):

  - Interval consumption = level before - level after (+ delivered litres).
  - Small rises between two readings are measurement noise. They are kept as
    small negative interval results so the sum telescopes: the net balance of a
    period is first level - last level, no matter how noisy the readings in
    between were. Summing only the drops would bias the rate upwards.
  - A rise above the jump threshold is not noise but a refill. Without a
    recorded delivery the interval is *unknown*: it contributes neither litres
    nor days and is reported as a probable delivery.
  - The rate is the net balance of all known intervals over their days in the
    last 365 days, clamped to >= 0.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

WINDOW_DAYS = 365

# A rise above max(JUMP_MIN_LITERS, JUMP_FRACTION_OF_MAX_LEVEL * highest level in the
# window) without a recorded delivery is a probable delivery. The floor keeps small
# tanks sensible, the fraction scales the threshold with big tanks.
JUMP_MIN_LITERS = 50.0
JUMP_FRACTION_OF_MAX_LEVEL = 0.05


@dataclass(frozen=True)
class Interval:
    from_id: Any
    to_id: Any
    from_at: datetime
    to_at: datetime
    days: float
    # None for an unknown interval (probable delivery or dismissed jump)
    consumption: Optional[float]
    state: str  # "known" | "probable_delivery" | "dismissed_jump"


@dataclass(frozen=True)
class ProbableDelivery:
    from_reading_id: Any
    to_reading_id: Any
    from_at: datetime
    to_at: datetime
    estimated_liters: int


@dataclass
class ConsumptionResult:
    liters_per_day: Optional[float] = None
    known_days: float = 0.0
    consumed_liters: float = 0.0
    intervals: list[Interval] = field(default_factory=list)
    probable: list[ProbableDelivery] = field(default_factory=list)


def _utc(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; the application stores UTC.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def jump_threshold(levels: Iterable[float]) -> float:
    return max(JUMP_MIN_LITERS, JUMP_FRACTION_OF_MAX_LEVEL * max(levels, default=0.0))


def assign_deliveries(readings_by_meter: dict, deliveries) -> dict:
    """Property deliveries -> the oil meter whose interval around the delivery shows the
    largest rise (ties: first meter in dict order). Deliveries outside every interval drop out."""
    assigned = {key: [] for key in readings_by_meter}
    sorted_points = {
        key: sorted(rs, key=lambda r: (_utc(r.read_at), r.id)) for key, rs in readings_by_meter.items()
    }
    for d in deliveries:
        at = _utc(d.delivered_at)
        best_key, best_rise = None, None
        for key, points in sorted_points.items():
            for prev, cur in zip(points, points[1:]):
                if _utc(prev.read_at) < at <= _utc(cur.read_at):
                    rise = float(cur.value) - float(prev.value)
                    if best_rise is None or rise > best_rise:
                        best_key, best_rise = key, rise
                    break
        if best_key is not None:
            assigned[best_key].append(d)
    return assigned


def compute_oil_consumption(readings, deliveries, dismissed, now: datetime) -> ConsumptionResult:
    """readings: objects with id/value/read_at; deliveries: objects with liters/delivered_at;
    dismissed: set of (from_reading_id, to_reading_id); now: end of the 365-day window."""
    since = _utc(now) - timedelta(days=WINDOW_DAYS)
    points = sorted(
        (r for r in readings if _utc(r.read_at) >= since),
        key=lambda r: (_utc(r.read_at), r.id),
    )
    result = ConsumptionResult()
    if len(points) < 2:
        return result

    dated = list(deliveries)
    threshold = jump_threshold(float(p.value) for p in points)
    net = 0.0
    for prev, cur in zip(points, points[1:]):
        prev_at, cur_at = _utc(prev.read_at), _utc(cur.read_at)
        span = (cur_at - prev_at).total_seconds() / 86400
        if span <= 0:
            continue
        before, after = float(prev.value), float(cur.value)
        consumption: Optional[float] = before - after
        state = "known"
        delivered = sum(float(d.liters) for d in dated if prev_at < _utc(d.delivered_at) <= cur_at)
        if delivered > 0:
            # Never negative with a recorded delivery; a delivery smaller than the rise
            # leaves the rest as (clamped) balance, see research R4.
            consumption = max(0.0, before - after + delivered)
        elif after - before > threshold:
            consumption = None
            if (prev.id, cur.id) in dismissed:
                state = "dismissed_jump"
            else:
                state = "probable_delivery"
                result.probable.append(
                    ProbableDelivery(prev.id, cur.id, prev_at, cur_at, round(after - before))
                )
        result.intervals.append(Interval(prev.id, cur.id, prev_at, cur_at, span, consumption, state))
        if consumption is not None:
            net += consumption
            result.known_days += span

    if result.known_days > 0:
        result.consumed_liters = max(0.0, net)
        result.liters_per_day = round(result.consumed_liters / result.known_days, 4)
    return result
