"""
Consumption statistics derived from reading intervals (feature 015).

Pure functions on plain lists (no database). Every meter type is reduced to the
same list of ``Interval`` objects that app.services.oil_consumption already
produces for oil tanks, so the 30-day rate, the monthly view and the yearly
figures cannot drift apart between meter types.

Water and electricity: only a rising value is consumption. An interval whose
value stays equal or falls is unknown (neither litres nor days), exactly like the
yearly average that existed before this module.
"""
from typing import Iterable, Optional

from app.services.oil_consumption import Interval, _utc


def intervals_from_readings(readings) -> list[Interval]:
    """Consecutive reading pairs of a meter whose value rises over time (water, electricity).

    readings: objects with id/value/read_at, any order. Pairs with identical or
    reversed timestamps produce no interval."""
    points = sorted(readings, key=lambda r: (_utc(r.read_at), r.id))
    intervals: list[Interval] = []
    for prev, cur in zip(points, points[1:]):
        prev_at, cur_at = _utc(prev.read_at), _utc(cur.read_at)
        days = (cur_at - prev_at).total_seconds() / 86400
        if days <= 0:
            continue
        delta = float(cur.value) - float(prev.value)
        if delta > 0:
            intervals.append(Interval(prev.id, cur.id, prev_at, cur_at, days, delta, "known"))
        else:
            intervals.append(Interval(prev.id, cur.id, prev_at, cur_at, days, None, "skipped"))
    return intervals


def yearly_rate(intervals: Iterable[Interval]) -> Optional[float]:
    """Net known consumption per known day; None without known days.

    Same number as the existing yearly average for water and electricity."""
    net = 0.0
    known_days = 0.0
    for iv in intervals:
        if iv.consumption is not None:
            net += iv.consumption
            known_days += iv.days
    if known_days <= 0:
        return None
    return round(net / known_days, 4)


def tank_group_key(meter):
    """Oil tanks that form one supply (one heating): today all active tanks of a property.

    The only place that decides this; separate groups per property would change it here."""
    return meter.property_id


def sum_rates(rates: Iterable[Optional[float]]) -> Optional[float]:
    """Sum of the rates that exist; None when no meter has one."""
    present = [r for r in rates if r is not None]
    if not present:
        return None
    return round(sum(present), 4)
