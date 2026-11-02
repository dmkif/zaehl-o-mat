import uuid
from types import SimpleNamespace
from datetime import date as date_type, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Meter, OilDelivery, OilMarketPrice, PriceEntry, Property,
    PropertyUser, Reading, User, UserRole, MeterType,
)
from app.routers.oil_deliveries import open_probable_deliveries
from app.schemas.oil_deliveries import ProbableDeliveryResponse
from app.services.buy_signal import compute_oil_buy_signal
from app.permissions import get_property_or_404, require_property_access
from app.schemas.dashboard import MonthlyResponse, YearlyResponse
from app.services.consumption_stats import (
    LOCAL_TZ,
    aggregate_overall, delivery_spend, group_range, group_statistics, last_interval_rate, merge_month_series, monthly_series, recent_rate, tank_group_key,
    trend_pct, yearly_rate, month_bounds, month_totals, unit_price_costs, year_summary,
)
from app.services.oil_loader import load_oil_results
from app.services.stats_loader import load_intervals

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _now() -> datetime:
    """The current instant; one place so tests can pin the clock."""
    return datetime.now(timezone.utc)


def _accessible_property_ids(user: User, db: Session) -> list[uuid.UUID]:
    if user.role in (UserRole.superadmin, UserRole.admin):
        return [r.id for r in db.query(Property.id).all()]
    assocs = db.query(PropertyUser.property_id).filter(PropertyUser.user_id == user.id).all()
    return [a.property_id for a in assocs]


@router.get("/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """High-level overview: property count, active meters, latest readings per meter."""
    prop_ids = _accessible_property_ids(current_user, db)

    meters = (
        db.query(Meter)
        .filter(Meter.property_id.in_(prop_ids), Meter.replaced_at.is_(None))
        .all()
    )

    # Batch latest-reading lookup: one query for all meters instead of N queries
    meter_ids = [m.id for m in meters]
    if meter_ids:
        latest_id_subq = (
            db.query(func.max(Reading.id).label("max_id"))
            .filter(Reading.meter_id.in_(meter_ids))
            .group_by(Reading.meter_id)
            .subquery()
        )
        latest_readings = (
            db.query(Reading)
            .filter(Reading.id.in_(db.query(latest_id_subq.c.max_id)))
            .all()
        )
        latest_map: dict[uuid.UUID, Reading] = {r.meter_id: r for r in latest_readings}
    else:
        latest_map = {}

    # Oil: one shared calculation for tile, range and forecast (app.services.oil_consumption)
    now = _now()
    oil_meters = [m for m in meters if m.meter_type == MeterType.oil]
    oil_results = load_oil_results(db, oil_meters, now)
    # Every other meter type: one batched readings query for all meters (feature 015)
    other_intervals = load_intervals(db, [m for m in meters if m.meter_type != MeterType.oil], now)

    meter_summaries = []
    rate_rows: list = []  # per meter rates for the per-property / overall statistics
    for m in meters:
        latest = latest_map.get(m.id)
        if m.meter_type == MeterType.oil:
            avg_daily = oil_results[m.id].liters_per_day
            intervals = oil_results[m.id].intervals
        else:
            intervals = other_intervals[m.id]
            avg_daily = yearly_rate(intervals)
        recent = recent_rate(intervals, now)
        rate_rows.append(SimpleNamespace(
            property_id=m.property_id, meter_type=m.meter_type.value, unit=m.unit.value,
            yearly=avg_daily, recent=recent,
        ))
        meter_summaries.append(
            {
                "meter_id": str(m.id),
                "meter_name": m.name,
                "meter_type": m.meter_type.value,
                "unit": m.unit.value,
                "property_id": str(m.property_id),
                "latest_value": str(latest.value) if latest else None,
                "latest_read_at": latest.read_at.isoformat() if latest else None,
                "avg_daily_consumption_365d": avg_daily,
                "avg_daily_consumption_30d": recent,
                "daily_consumption_last_interval": last_interval_rate(intervals),
                "trend_30d_vs_365d_pct": trend_pct(recent, avg_daily),
            }
        )

    # Oil Reichweite per tank group (oil meter = direct tank level reading). All active tanks of
    # a property form one group: sum of the levels / sum of the rates (spec 015, FR-024).
    oil_reichweite: dict[str, Optional[int]] = {}
    oil_reichweite_30d: dict[str, int] = {}
    tank_groups: dict[uuid.UUID, list[Meter]] = {}
    for m in oil_meters:
        tank_groups.setdefault(tank_group_key(m), []).append(m)
    for key, tanks in tank_groups.items():
        levels = [float(latest_map[t.id].value) if latest_map.get(t.id) else None for t in tanks]
        yearly_rates = [oil_results[t.id].liters_per_day for t in tanks]
        oil_reichweite[str(key)] = group_range(levels, yearly_rates)
        recent_rates = [recent_rate(oil_results[t.id].intervals, now) for t in tanks]
        days_30d = group_range(levels, recent_rates)
        if days_30d is not None:
            oil_reichweite_30d[str(key)] = days_30d

    oil_probable = open_probable_deliveries(db, prop_ids, now)

    # Per property and overall figures: only properties the caller may access (spec 015, FR-016/021)
    prop_names = (
        {p.id: p.name for p in db.query(Property).filter(Property.id.in_(prop_ids)).all()} if prop_ids else {}
    )
    stats_by_property: dict[uuid.UUID, dict] = {}
    for pid in {r.property_id for r in rate_rows}:
        stats_by_property[pid] = group_statistics([r for r in rate_rows if r.property_id == pid])
    statistics = {
        "by_property": {
            str(pid): {"name": prop_names.get(pid), "types": types}
            for pid, types in stats_by_property.items() if types
        },
        "overall": (aggregate_overall(stats_by_property) or None) if len(prop_ids) >= 2 else None,
    }

    # Latest oil market price + buy signal
    oil_price = (
        db.query(OilMarketPrice)
        .order_by(OilMarketPrice.price_date.desc())
        .first()
    )
    buy_signal = compute_oil_buy_signal(db)

    return {
        "property_count": len(prop_ids),
        "active_meter_count": len(meters),
        "meters": meter_summaries,
        "oil_reichweite": oil_reichweite,
        "oil_reichweite_30d": oil_reichweite_30d,
        "statistics": statistics,
        "oil_probable_deliveries": [ProbableDeliveryResponse(**p).model_dump(mode="json") for p in oil_probable],
        "oil_market_price": {
            "price_per_100l": str(oil_price.price_per_100l) if oil_price else None,
            "date": oil_price.price_date.isoformat() if oil_price else None,
            "source": oil_price.source if oil_price else None,
            "buy_signal": buy_signal,
        },
    }


def _resolve_scope(
    db: Session, user: User, meter_id: Optional[uuid.UUID], property_id: Optional[uuid.UUID]
) -> tuple[dict, list[Meter]]:
    """Meters a statistics request covers. Access is checked before any reading is read.

    meter_id: that meter (property access required); property_id: its active meters;
    neither: the active meters of every property the caller may access."""
    if meter_id is not None and property_id is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Use meter_id or property_id, not both")
    if meter_id is not None:
        meter = db.query(Meter).filter(Meter.id == meter_id).first()
        if not meter:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        require_property_access(db, meter.property_id, user, "read")
        return {"kind": "meter", "id": str(meter_id)}, [meter]
    if property_id is not None:
        get_property_or_404(db, property_id)
        require_property_access(db, property_id, user, "read")
        meters = db.query(Meter).filter(Meter.property_id == property_id, Meter.replaced_at.is_(None)).all()
        return {"kind": "property", "id": str(property_id)}, meters
    prop_ids = _accessible_property_ids(user, db)
    meters = (
        db.query(Meter).filter(Meter.property_id.in_(prop_ids), Meter.replaced_at.is_(None)).all()
        if prop_ids else []
    )
    return {"kind": "all", "id": None}, meters


@router.get("/monthly", response_model=MonthlyResponse)
def monthly_consumption(
    meter_id: Optional[uuid.UUID] = None,
    property_id: Optional[uuid.UUID] = None,
    months: int = Query(12, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Consumption per local calendar month, with the same month of the previous year."""
    scope, meters = _resolve_scope(db, current_user, meter_id, property_id)
    now = _now()
    # months to show + one year back for the previous-year comparison
    intervals = load_intervals(db, meters, now, window_days=(months + 12) * 31 + 1)
    prices = _price_entries(db, [m.id for m in meters if m.meter_type != MeterType.oil])
    by_type: dict[MeterType, list] = {}
    units: dict[MeterType, str] = {}
    for m in meters:
        rows = monthly_series(intervals[m.id], now, months)
        if m.meter_type != MeterType.oil:
            # water / electricity: consumption x the unit price valid at the end of the month
            costs, _ = unit_price_costs([(r.month, r.value) for r in rows], prices.get(m.id, []))
            for r in rows:
                r.cost = costs.get(r.month)
        by_type.setdefault(m.meter_type, []).append(rows)
        units.setdefault(m.meter_type, m.unit.value)
    oil_property_ids = {m.property_id for m in meters if m.meter_type == MeterType.oil}
    deliveries = _deliveries(db, oil_property_ids)
    series = []
    for mt, rows in sorted(by_type.items(), key=lambda kv: kv[0].value):
        merged = merge_month_series(rows)
        if mt == MeterType.oil:
            # oil: the money spent on deliveries in that month (deliveries belong to the property)
            for row in merged:
                year, month = int(row.month[:4]), int(row.month[5:7])
                m_start, m_end = month_bounds(year, month)
                spends = [
                    delivery_spend(deliveries.get(pid, []), m_start - timedelta(microseconds=1), m_end - timedelta(microseconds=1))[0]
                    for pid in oil_property_ids
                ]
                priced = [x for x in spends if x is not None]
                row.cost = round(sum(priced), 2) if priced else None
        series.append({"meter_type": mt.value, "unit": units[mt], "months": [vars(row) for row in merged]})
    return {"scope": scope, "series": series}


def _price_entries(db: Session, meter_ids: list) -> dict:
    grouped: dict = {}
    if not meter_ids:
        return grouped
    for p in db.query(PriceEntry).filter(PriceEntry.meter_id.in_(meter_ids)).all():
        grouped.setdefault(p.meter_id, []).append(p)
    return grouped


def _deliveries(db: Session, property_ids) -> dict:
    grouped: dict = {}
    if not property_ids:
        return grouped
    for d in db.query(OilDelivery).filter(OilDelivery.property_id.in_(list(property_ids))).all():
        grouped.setdefault(d.property_id, []).append(d)
    return grouped


@router.get("/yearly", response_model=YearlyResponse)
def yearly_consumption(
    meter_id: Optional[uuid.UUID] = None,
    property_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Current calendar year to date against the same period of the previous year, with cost
    where a price is known (oil: money spent on deliveries; other types: consumption x unit price)."""
    scope, meters = _resolve_scope(db, current_user, meter_id, property_id)
    now = _now()
    local_year = now.astimezone(LOCAL_TZ).year
    year_start, _ = month_bounds(local_year, 1)
    prev_start, _ = month_bounds(local_year - 1, 1)
    intervals = load_intervals(db, meters, now, window_days=(now - prev_start).days + 2)
    prices = _price_entries(db, [m.id for m in meters if m.meter_type != MeterType.oil])
    oil_property_ids = {m.property_id for m in meters if m.meter_type == MeterType.oil}
    deliveries = _deliveries(db, oil_property_ids)

    by_type: dict[MeterType, list[Meter]] = {}
    for m in meters:
        by_type.setdefault(m.meter_type, []).append(m)
    series = []
    for mt, group in sorted(by_type.items(), key=lambda kv: kv[0].value):
        summaries = [year_summary(intervals[m.id], now) for m in group]
        current = [x["year_to_date"] for x in summaries if x["year_to_date"] is not None]
        previous = [x["previous_year_same_period"] for x in summaries if x["previous_year_same_period"] is not None]
        ytd = round(sum(current), 3) if current else None
        prev = round(sum(previous), 3) if previous else None
        delta = round((ytd / prev - 1) * 100, 1) if ytd is not None and prev is not None and prev > 0 else None
        cost, complete = None, False
        if mt == MeterType.oil:
            results = [delivery_spend(deliveries.get(pid, []), year_start, now) for pid in oil_property_ids]
            priced = [c for c, _ in results if c is not None]
            cost = round(sum(priced), 2) if priced else None
            complete = bool(priced) and all(ok for _, ok in results)
        else:
            total, all_complete, any_cost = 0.0, True, False
            for m in group:
                totals = month_totals(intervals[m.id])
                months_this_year = [(k, v["value"]) for k, v in totals.items() if k.startswith(f"{local_year:04d}-")]
                costs, ok = unit_price_costs(months_this_year, prices.get(m.id, []))
                if costs:
                    any_cost = True
                    total += sum(costs.values())
                all_complete = all_complete and ok
            cost = round(total, 2) if any_cost else None
            complete = any_cost and all_complete
        series.append({
            "meter_type": mt.value, "unit": group[0].unit.value,
            "year_to_date": ytd, "previous_year_same_period": prev, "delta_pct": delta,
            "cost": cost, "cost_complete": complete,
        })
    return {"scope": scope, "as_of": now.isoformat(), "series": series}


@router.get("/consumption/{meter_id}")
def meter_consumption(
    meter_id: uuid.UUID,
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Time-series consumption data for a single meter (difference between consecutive readings)."""
    meter = db.query(Meter).filter(Meter.id == meter_id).first()
    if not meter:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    prop_ids = _accessible_property_ids(current_user, db)
    if meter.property_id not in prop_ids:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    q = db.query(Reading).filter(Reading.meter_id == meter_id)
    if from_dt:
        q = q.filter(Reading.read_at >= from_dt)
    if to_dt:
        q = q.filter(Reading.read_at <= to_dt)
    readings = q.order_by(Reading.read_at.asc()).all()

    data = []
    for i in range(1, len(readings)):
        prev, curr = readings[i - 1], readings[i]
        delta = curr.value - prev.value
        if delta >= 0:
            data.append(
                {
                    "from": prev.read_at.isoformat(),
                    "to": curr.read_at.isoformat(),
                    "consumption": str(delta),
                    "unit": meter.unit.value,
                }
            )

    return {"meter_id": str(meter_id), "data": data}


@router.get("/cost-forecast/{meter_id}")
def cost_forecast(
    meter_id: uuid.UUID,
    months_ahead: int = Query(3, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Simple linear extrapolation of consumption + latest price per unit.
    Returns forecast cost in EUR for each upcoming month.
    """
    meter = db.query(Meter).filter(Meter.id == meter_id).first()
    if not meter:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    prop_ids = _accessible_property_ids(current_user, db)
    if meter.property_id not in prop_ids:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if meter.meter_type == MeterType.oil:
        # A tank level falls with use and jumps on refills: same rate as the dashboard tile
        daily_consumption = load_oil_results(db, [meter], datetime.now(timezone.utc))[meter.id].liters_per_day
        if daily_consumption is None:
            return {"meter_id": str(meter_id), "forecast": [], "insufficient_data": True}
    else:
        # Use last 90 days of readings for trend
        since = datetime.now(timezone.utc) - timedelta(days=90)
        readings = (
            db.query(Reading)
            .filter(Reading.meter_id == meter_id, Reading.read_at >= since)
            .order_by(Reading.read_at.asc())
            .all()
        )

        if len(readings) < 2:
            return {"meter_id": str(meter_id), "forecast": [], "insufficient_data": True}

        total_consumption = readings[-1].value - readings[0].value
        days_span = (readings[-1].read_at - readings[0].read_at).days or 1
        daily_consumption = float(total_consumption) / days_span

    # Latest price per unit
    price_entry = (
        db.query(PriceEntry)
        .filter(PriceEntry.meter_id == meter_id)
        .order_by(PriceEntry.valid_from.desc())
        .first()
    )
    price_per_unit = float(price_entry.price_per_unit) if price_entry else None

    forecast = []
    now = datetime.now(timezone.utc)
    for m in range(1, months_ahead + 1):
        month_consumption = daily_consumption * 30
        cost = month_consumption * price_per_unit if price_per_unit else None
        forecast.append(
            {
                "month": (now.replace(day=1) + timedelta(days=31 * m)).strftime("%Y-%m"),
                "estimated_consumption": round(month_consumption, 3),
                "unit": meter.unit.value,
                "estimated_cost_eur": round(cost, 2) if cost else None,
            }
        )

    return {"meter_id": str(meter_id), "forecast": forecast, "insufficient_data": False}


@router.get("/property/{property_id}/type-aggregates")
def property_type_aggregates(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cumulative consumption per meter type for a property over the last 365 days."""
    from fastapi import HTTPException, status

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    prop_ids = _accessible_property_ids(current_user, db)
    if property_id not in prop_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    meters = (
        db.query(Meter)
        .filter(Meter.property_id == property_id, Meter.replaced_at.is_(None))
        .all()
    )

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=365)
    oil_results = load_oil_results(db, [m for m in meters if m.meter_type == MeterType.oil], now)

    # Unit mapping per meter type
    type_unit = {
        MeterType.water: "m³",
        MeterType.electricity: "kWh",
        MeterType.oil: "L",
    }

    # Aggregate per type
    type_data: dict[MeterType, dict] = {}
    for m in meters:
        if m.meter_type == MeterType.oil:
            # Tank level: net balance of the known intervals (refills are not consumption)
            consumption = oil_results[m.id].consumed_liters
        else:
            readings = (
                db.query(Reading)
                .filter(Reading.meter_id == m.id, Reading.read_at >= since)
                .order_by(Reading.read_at.asc())
                .all()
            )
            # Electricity and water increase over time → sum positive deltas.
            consumption = sum(
                max(0.0, float(readings[i].value) - float(readings[i - 1].value))
                for i in range(1, len(readings))
            )

        if m.meter_type not in type_data:
            type_data[m.meter_type] = {
                "meter_type": m.meter_type.value,
                "total_consumption": 0.0,
                "unit": type_unit.get(m.meter_type, m.unit.value),
                "meter_count": 0,
                "period_days": 365,
            }
        type_data[m.meter_type]["total_consumption"] += consumption
        type_data[m.meter_type]["meter_count"] += 1

    aggregates = []
    for entry in type_data.values():
        entry["total_consumption"] = round(entry["total_consumption"], 3)
        aggregates.append(entry)

    return {"property_id": str(property_id), "aggregates": aggregates}


@router.get("/oil-price-history")
def oil_price_history(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # noqa: ARG001
):
    """Historical oil market prices with a 30-day linear trend forecast."""
    since = date_type.today() - timedelta(days=days)
    rows = (
        db.query(OilMarketPrice)
        .filter(OilMarketPrice.price_date >= since)
        .order_by(OilMarketPrice.price_date.asc())
        .all()
    )

    history = [
        {"date": r.price_date.isoformat(), "price": float(r.price_per_100l)}
        for r in rows
    ]

    forecast: list[dict] = []
    if len(history) >= 2:
        n = len(history)
        xs = list(range(n))
        ys = [h["price"] for h in history]
        sx = sum(xs)
        sy = sum(ys)
        sxy = sum(x * y for x, y in zip(xs, ys))
        sx2 = sum(x * x for x in xs)
        denom = n * sx2 - sx * sx
        if denom != 0:
            slope = (n * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / n
            last_date = rows[-1].price_date
            for i in range(1, 31):
                fut_date = last_date + timedelta(days=i)
                fut_price = round(intercept + slope * (n - 1 + i), 2)
                forecast.append({"date": fut_date.isoformat(), "price": fut_price})

    return {"history": history, "forecast": forecast}
