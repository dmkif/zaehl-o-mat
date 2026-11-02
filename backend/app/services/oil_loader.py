"""Database glue for app.services.oil_consumption: loads what the calculation needs."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Meter, OilDelivery, OilDeliveryDismissal
from app.services.reading_selection import counting_readings
from app.services.oil_consumption import (
    WINDOW_DAYS, ConsumptionResult, assign_deliveries, compute_oil_consumption,
)


def load_oil_results(
    db: Session, meters: list[Meter], now: datetime, window_days: int = WINDOW_DAYS
) -> dict[uuid.UUID, ConsumptionResult]:
    """Consumption result per oil meter (all given meters must be oil meters).

    Deliveries belong to the property; with several tanks in one property a delivery
    is applied to the tank that shows the largest rise (assign_deliveries).
    """
    since = now - timedelta(days=window_days)
    results: dict[uuid.UUID, ConsumptionResult] = {}
    by_property: dict[uuid.UUID, list[Meter]] = {}
    for m in sorted(meters, key=lambda m: m.name):
        by_property.setdefault(m.property_id, []).append(m)

    all_readings = counting_readings(db, [m.id for m in meters], since)
    for property_id, prop_meters in by_property.items():
        readings = {m.id: all_readings[m.id] for m in prop_meters}
        deliveries = db.query(OilDelivery).filter(OilDelivery.property_id == property_id).all()
        assigned = assign_deliveries(readings, deliveries)
        dismissed = {
            (d.from_reading_id, d.to_reading_id)
            for d in db.query(OilDeliveryDismissal)
            .filter(OilDeliveryDismissal.meter_id.in_([m.id for m in prop_meters]))
            .all()
        }
        for m in prop_meters:
            results[m.id] = compute_oil_consumption(readings[m.id], assigned[m.id], dismissed, now, window_days)
    return results
