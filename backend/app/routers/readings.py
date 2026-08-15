import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Meter, Reading, ReadingSource, User
from app.permissions import accessible_property_ids, get_meter_with_access, is_global_admin
from app.schemas.readings import ReadingCreate, ReadingResponse, ReadingUpdate

router = APIRouter(prefix="/properties/{property_id}/meters/{meter_id}/readings", tags=["readings"])


def _find_duplicate_reading(db: Session, image_hash: str, user: User) -> Reading | None:
    """Find an existing reading with the same image hash.

    Scoped to properties the user can access — the duplicate check must not
    leak readings (value, timestamp) from other tenants.
    """
    q = db.query(Reading).join(Meter, Reading.meter_id == Meter.id).filter(
        Reading.image_hash == image_hash
    )
    if not is_global_admin(user):
        q = q.filter(Meter.property_id.in_(accessible_property_ids(user)))
    return q.first()


@router.get("/", response_model=List[ReadingResponse])
def list_readings(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_meter_with_access(db, property_id, meter_id, current_user, level="read")
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
    get_meter_with_access(db, property_id, meter_id, current_user, level="write")

    # Compute SHA-256 of the uploaded image so we can detect duplicate readings
    # in future import sessions.
    image_hash: str | None = None
    if body.image_path:
        img_file = Path(settings.upload_path) / Path(body.image_path).name
        try:
            image_hash = hashlib.sha256(img_file.read_bytes()).hexdigest()
        except OSError:
            pass  # file missing → proceed without hash

    # Duplicate check: if this exact image (same hash) was already saved, reject.
    if image_hash:
        existing = _find_duplicate_reading(db, image_hash, current_user)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "duplicate_image",
                    "reading_id": existing.id,
                    "value": str(existing.value),
                    "read_at": existing.read_at.isoformat(),
                },
            )

    reading = Reading(
        meter_id=meter_id,
        value=body.value,
        read_at=body.read_at or datetime.now(timezone.utc),
        source=body.source or ReadingSource.manual,
        image_path=body.image_path,
        serial_image_path=body.serial_image_path,
        image_hash=image_hash,
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
    get_meter_with_access(db, property_id, meter_id, current_user, level="delete")
    reading = db.query(Reading).filter(Reading.id == reading_id, Reading.meter_id == meter_id).first()
    if not reading:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    image_path = reading.image_path
    serial_image_path = reading.serial_image_path
    db.delete(reading)
    db.commit()
    if image_path:
        img_file = Path(settings.upload_path) / Path(image_path).name
        img_file.unlink(missing_ok=True)
    if serial_image_path:
        serial_file = Path(settings.upload_path) / Path(serial_image_path).name
        serial_file.unlink(missing_ok=True)


@router.patch("/{reading_id}", response_model=ReadingResponse)
def update_reading(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    reading_id: int,
    body: ReadingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_meter_with_access(db, property_id, meter_id, current_user, level="write")
    reading = db.query(Reading).filter(Reading.id == reading_id, Reading.meter_id == meter_id).first()
    if not reading:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # PATCH semantics: only fields present in the request body are changed;
    # note may be explicitly set to null to clear it.
    updates = body.model_dump(exclude_unset=True)
    if "value" in updates and updates["value"] is not None:
        reading.value = updates["value"]
    if "read_at" in updates and updates["read_at"] is not None:
        reading.read_at = updates["read_at"]
    if "note" in updates:
        reading.note = updates["note"]
    db.commit()
    db.refresh(reading)
    return reading
