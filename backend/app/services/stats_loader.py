"""Database glue for the consumption statistics (feature 015).

Which readings count is decided in app.services.reading_selection (one place).
"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Meter, MeterType
from app.services.consumption_stats import intervals_from_readings
from app.services.oil_consumption import WINDOW_DAYS, Interval
from app.services.oil_loader import load_oil_results
from app.services.reading_selection import counting_readings


def load_intervals(
    db: Session, meters: list[Meter], now: datetime, window_days: int = WINDOW_DAYS
) -> dict[uuid.UUID, list[Interval]]:
    """Reading intervals per meter over the window: oil through the balance rules of
    feature 005 (deliveries, probable deliveries, noise), other types from rising values."""
    result: dict[uuid.UUID, list[Interval]] = {}
    oil = [m for m in meters if m.meter_type == MeterType.oil]
    other = [m for m in meters if m.meter_type != MeterType.oil]
    for m, res in load_oil_results(db, oil, now, window_days).items():
        result[m] = res.intervals
    readings = counting_readings(db, [m.id for m in other], now - timedelta(days=window_days))
    for m in other:
        result[m.id] = intervals_from_readings(readings[m.id])
    return result
