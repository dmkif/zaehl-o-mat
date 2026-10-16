"""Database glue for app.services.oil_consumption: loads what the calculation needs."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Meter, OilDelivery, OilDeliveryDismissal, Reading
from app.services.oil_consumption import (
    WINDOW_DAYS, ConsumptionResult, assign_deliveries, compute_oil_consumption,
)


def load_oil_results(
    db: Session, meters: list[Meter], now: datetime
) -> dict[uuid.UUID, ConsumptionResult]:
    """Consumption result per oil meter (all given meters must be oil meters).

    Deliveries belong to the property; with several tanks in one property a delivery
    is applied to the tank that shows the largest rise (assign_deliveries).
    """
    since = now - timedelta(days=WINDOW_DAYS)
    results: dict[uuid.UUID, ConsumptionResult] = {}
    by_property: dict[uuid.UUID, list[Meter]] = {}
    for m in sorted(meters, key=lambda m: m.name):
        by_property.setdefault(m.property_id, []).append(m)

    for property_id, prop_meters in by_property.items():
        readings = {
            m.id: db.query(Reading).filter(Reading.meter_id == m.id, Reading.read_at >= since).all()
            for m in prop_meters
        }
        deliveries = db.query(OilDelivery).filter(OilDelivery.property_id == property_id).all()
        assigned = assign_deliveries(readings, deliveries)
        dismissed = {
            (d.from_reading_id, d.to_reading_id)
            for d in db.query(OilDeliveryDismissal)
            .filter(OilDeliveryDismissal.meter_id.in_([m.id for m in prop_meters]))
            .all()
        }
        for m in prop_meters:
            results[m.id] = compute_oil_consumption(readings[m.id], assigned[m.id], dismissed, now)
    return results
