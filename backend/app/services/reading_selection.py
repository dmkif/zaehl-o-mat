"""The single selection of readings that count for statistics, ranges and oil consumption.

Today every reading counts; a later feature (reading release, specs/016-roles-and-release)
restricts it here and nowhere else.
"""
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Reading


def counting_readings(
    db: Session, meter_ids: list[uuid.UUID], since: datetime
) -> dict[uuid.UUID, list[Reading]]:
    """Readings per meter since ``since`` (one query for all meters)."""
    grouped: dict[uuid.UUID, list[Reading]] = {mid: [] for mid in meter_ids}
    if not meter_ids:
        return grouped
    rows = (
        db.query(Reading)
        .filter(Reading.meter_id.in_(meter_ids), Reading.read_at >= since)
        .order_by(Reading.meter_id, Reading.read_at.asc(), Reading.id.asc())
        .all()
    )
    for r in rows:
        grouped[r.meter_id].append(r)
    return grouped
