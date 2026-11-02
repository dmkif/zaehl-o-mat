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
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

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


RECENT_DAYS = 30


def recent_rate(intervals: Iterable[Interval], now: datetime, days: int = RECENT_DAYS) -> Optional[float]:
    """Net consumption per day over the known intervals that end within the last ``days`` days.

    An interval crossing the window start counts whole, so sparse readings (one per month)
    still give a rate. Unknown intervals are skipped like in the yearly rate; the result is
    clamped to >= 0 (oil reading noise). None when no known interval ends in the window."""
    since = _utc(now) - timedelta(days=days)
    net = 0.0
    known_days = 0.0
    for iv in intervals:
        if iv.consumption is not None and _utc(iv.to_at) > since:
            net += iv.consumption
            known_days += iv.days
    if known_days <= 0:
        return None
    return round(max(0.0, net) / known_days, 4)


def last_interval_rate(intervals: Iterable[Interval]) -> Optional[float]:
    """Rate of the newest interval (since the last reading); None if it is unknown."""
    ordered = list(intervals)
    if not ordered:
        return None
    last = ordered[-1]
    if last.consumption is None or last.days <= 0:
        return None
    return round(max(0.0, last.consumption) / last.days, 4)


def trend_pct(recent: Optional[float], yearly: Optional[float]) -> Optional[float]:
    """Percent difference of the recent rate to the yearly rate, one decimal; None if not defined."""
    if recent is None or yearly is None or yearly <= 0:
        return None
    return round((recent / yearly - 1) * 100, 1)


# ── Monthly view ──────────────────────────────────────────────────────────────

LOCAL_TZ = ZoneInfo("Europe/Berlin")
ESTIMATED_INTERVAL_DAYS = 60
_COMPLETE_TOLERANCE_SECONDS = 1.0


@dataclass
class MonthValue:
    month: str  # YYYY-MM in the local calendar
    value: Optional[float]
    state: str  # "complete" | "partial" | "no_data"
    estimated: bool = False
    previous_year: Optional[dict] = None
    delta_pct: Optional[float] = None
    cost: Optional[float] = None


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of a local calendar month as UTC instants."""
    start = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_totals(intervals: Iterable[Interval]) -> dict[str, dict]:
    """Per local calendar month: consumption share, covered seconds and the share that
    comes from long intervals. Each known interval is distributed over the months it spans
    in proportion to time, so the shares of one interval add up to its consumption."""
    totals: dict[str, dict] = {}
    for iv in intervals:
        if iv.consumption is None:
            continue
        start, end = _utc(iv.from_at), _utc(iv.to_at)
        total_seconds = (end - start).total_seconds()
        if total_seconds <= 0:
            continue
        long_interval = iv.days > ESTIMATED_INTERVAL_DAYS
        local_start = start.astimezone(LOCAL_TZ)
        year, month = local_start.year, local_start.month
        while True:
            m_start, m_end = _month_bounds(year, month)
            if m_start >= end:
                break
            overlap = (min(end, m_end) - max(start, m_start)).total_seconds()
            if overlap > 0:
                share = iv.consumption * overlap / total_seconds
                row = totals.setdefault(
                    _month_key(year, month), {"value": 0.0, "covered": 0.0, "long": 0.0}
                )
                row["value"] += share
                row["covered"] += overlap
                if long_interval:
                    row["long"] += share
            year, month = _shift_month(year, month, 1)
    return totals


def _month_state(year: int, month: int, row: Optional[dict]) -> str:
    if row is None or row["covered"] <= 0:
        return "no_data"
    m_start, m_end = _month_bounds(year, month)
    month_seconds = (m_end - m_start).total_seconds()
    return "complete" if row["covered"] >= month_seconds - _COMPLETE_TOLERANCE_SECONDS else "partial"


def monthly_series(intervals: Iterable[Interval], now: datetime, months: int = 12) -> list[MonthValue]:
    """The last ``months`` local calendar months up to and including the month of ``now``,
    oldest first. A month without a covering known interval is ``no_data`` with value None
    (never 0); the current month is ``partial``. The same month one year earlier is attached
    when it has data; the percent difference only when both months are complete."""
    totals = month_totals(intervals)
    local_now = _utc(now).astimezone(LOCAL_TZ)
    rows: list[MonthValue] = []
    for back in range(months - 1, -1, -1):
        year, month = _shift_month(local_now.year, local_now.month, -back)
        data = totals.get(_month_key(year, month))
        state = _month_state(year, month, data)
        value = round(data["value"], 6) if state != "no_data" else None
        estimated = bool(state != "no_data" and data["value"] > 0 and data["long"] / data["value"] > 0.5)
        py_year, py_month = _shift_month(year, month, -12)
        py_data = totals.get(_month_key(py_year, py_month))
        py_state = _month_state(py_year, py_month, py_data)
        previous = None
        delta = None
        if py_state != "no_data":
            py_value = round(py_data["value"], 6)
            previous = {"value": py_value, "state": py_state}
            if state == "complete" and py_state == "complete" and py_value > 0:
                delta = round((value / py_value - 1) * 100, 1)
        rows.append(MonthValue(_month_key(year, month), value, state, estimated, previous, delta))
    return rows


def merge_month_series(series: list[list[MonthValue]]) -> list[MonthValue]:
    """Add the monthly series of several meters of one meter type.

    Value and previous-year value are sums over the meters that have data. A month is
    ``complete`` only if every meter with data is complete; the percent difference follows
    the same rule as for one meter. Cost is summed when every contributing month has one."""
    if not series:
        return []
    merged: list[MonthValue] = []
    for index, first in enumerate(series[0]):
        rows = [s[index] for s in series]
        with_data = [r for r in rows if r.state != "no_data"]
        if not with_data:
            merged.append(MonthValue(first.month, None, "no_data"))
            continue
        value = round(sum(r.value for r in with_data), 6)
        state = "complete" if all(r.state == "complete" for r in with_data) else "partial"
        prev_rows = [r.previous_year for r in rows if r.previous_year is not None]
        previous = None
        delta = None
        if prev_rows:
            prev_value = round(sum(p["value"] for p in prev_rows), 6)
            prev_state = "complete" if all(p["state"] == "complete" for p in prev_rows) else "partial"
            previous = {"value": prev_value, "state": prev_state}
            if state == "complete" and prev_state == "complete" and prev_value > 0:
                delta = round((value / prev_value - 1) * 100, 1)
        costs = [r.cost for r in with_data]
        cost = round(sum(costs), 2) if all(c is not None for c in costs) else None
        merged.append(MonthValue(
            first.month, value, state, any(r.estimated for r in with_data), previous, delta, cost,
        ))
    return merged


def group_range(levels: list[Optional[float]], rates: list[Optional[float]]) -> Optional[int]:
    """Days a tank group lasts: sum of the current levels / sum of the rates, rounded down.

    None when a tank has no level or no rate (a partial sum would promise too many days),
    or when the summed rate is not positive. A standby tank with a rate of 0 is fine."""
    if not levels or len(levels) != len(rates):
        return None
    if any(level is None for level in levels) or any(rate is None for rate in rates):
        return None
    total_rate = sum(rates)
    if total_rate <= 0:
        return None
    return math.floor(sum(levels) / total_rate)
