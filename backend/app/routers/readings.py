import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Meter, Property, PropertyUser, PropertyUserRole, Reading, ReadingSource, User, UserRole
from app.schemas.readings import ReadingCreate, ReadingResponse

router = APIRouter(prefix="/properties/{property_id}/meters/{meter_id}/readings", tags=["readings"])


def _get_meter_with_access(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    user: User,
    db: Session,
    require_write: bool = False,
) -> Meter:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")

    if user.role in (UserRole.superadmin, UserRole.admin):
        return meter

    filter_roles = (
        [PropertyUserRole.admin, PropertyUserRole.manager]
        if require_write
        else [PropertyUserRole.admin, PropertyUserRole.manager, PropertyUserRole.user]
    )
    assoc = (
        db.query(PropertyUser)
        .filter(
            PropertyUser.property_id == property_id,
            PropertyUser.user_id == user.id,
            PropertyUser.role.in_(filter_roles),
        )
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return meter


@router.get("/", response_model=List[ReadingResponse])
def list_readings(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(200, le=10000),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_meter_with_access(property_id, meter_id, current_user, db)
    q = db.query(Reading).filter(Reading.meter_id == meter_id)
    if from_dt:
        q = q.filter(Reading.read_at >= from_dt)
    if to_dt:
        q = q.filter(Reading.read_at <= to_dt)
    return q.order_by(Reading.read_at.desc()).offset(offset).limit(limit).all()


@router.post("/", response_model=ReadingResponse, status_code=status.HTTP_201_CREATED)
def create_reading(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    body: ReadingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_meter_with_access(property_id, meter_id, current_user, db, require_write=True)
    reading = Reading(
        meter_id=meter_id,
        value=body.value,
        read_at=body.read_at or datetime.now(timezone.utc),
        source=body.source or ReadingSource.manual,
        image_path=body.image_path,
        note=body.note,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


@router.delete("/{reading_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reading(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    reading_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_meter_with_access(property_id, meter_id, current_user, db, require_write=True)
    reading = db.query(Reading).filter(Reading.id == reading_id, Reading.meter_id == meter_id).first()
    if not reading:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(reading)
    db.commit()
