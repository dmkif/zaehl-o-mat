import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Meter, OilDelivery, OilMarketPrice, PriceEntry, Property,
    PropertyUser, Reading, User, UserRole, MeterType,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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

    meter_summaries = []
    for m in meters:
        latest = (
            db.query(Reading)
            .filter(Reading.meter_id == m.id)
            .order_by(Reading.read_at.desc())
            .first()
        )
        meter_summaries.append(
            {
                "meter_id": str(m.id),
                "meter_name": m.name,
                "meter_type": m.meter_type.value,
                "unit": m.unit.value,
                "property_id": str(m.property_id),
                "latest_value": str(latest.value) if latest else None,
                "latest_read_at": latest.read_at.isoformat() if latest else None,
            }
        )

    # Latest oil market price
    oil_price = (
        db.query(OilMarketPrice)
        .order_by(OilMarketPrice.price_date.desc())
        .first()
    )

    return {
        "property_count": len(prop_ids),
        "active_meter_count": len(meters),
        "meters": meter_summaries,
        "oil_market_price": {
            "price_per_100l": str(oil_price.price_per_100l) if oil_price else None,
            "date": oil_price.price_date.isoformat() if oil_price else None,
            "source": oil_price.source if oil_price else None,
        },
    }


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

    # Use last 90 days of readings for trend
    since = datetime.utcnow() - timedelta(days=90)
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
    now = datetime.utcnow()
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
