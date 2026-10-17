import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Meter, User
from app.permissions import get_property_or_404, require_property_access
from app.schemas.meters import MeterCreate, MeterUpdate, MeterResponse

router = APIRouter(prefix="/properties/{property_id}/meters", tags=["meters"])


def _check_replacement(db: Session, property_id: uuid.UUID, meter_id: uuid.UUID, replaced_by_id: uuid.UUID | None) -> None:
    """F-037: the successor must be another meter of the same property."""
    if replaced_by_id is None:
        return
    ok = replaced_by_id != meter_id and (
        db.query(Meter.id).filter(Meter.id == replaced_by_id, Meter.property_id == property_id).first() is not None
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="replaced_by_id must reference another meter of the same property",
        )


def _config_to_text(values: dict) -> dict:
    """``integration_config`` is an object in the API and JSON text in the column."""
    if values.get("integration_config") is not None:
        values["integration_config"] = json.dumps(values["integration_config"])
    return values


@router.get("/", response_model=List[MeterResponse])
def list_meters(
    property_id: uuid.UUID,
    include_replaced: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="read")
    q = db.query(Meter).filter(Meter.property_id == property_id)
    if not include_replaced:
        q = q.filter(Meter.replaced_at.is_(None))
    return q.all()


@router.post("/", response_model=MeterResponse, status_code=status.HTTP_201_CREATED)
def create_meter(
    property_id: uuid.UUID,
    body: MeterCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="write")
    meter = Meter(id=uuid.uuid4(), property_id=property_id, **_config_to_text(body.model_dump()))
    db.add(meter)
    db.flush()
    response = MeterResponse.model_validate(meter)  # before commit (F-035)
    db.commit()
    return response


@router.get("/{meter_id}", response_model=MeterResponse)
def get_meter(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="read")
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return meter


@router.patch("/{meter_id}", response_model=MeterResponse)
def update_meter(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    body: MeterUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="write")
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if meter.replaced_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot update a replaced meter")
    updates = _config_to_text(body.model_dump(exclude_unset=True))
    _check_replacement(db, property_id, meter.id, updates.get("replaced_by_id"))
    for field, value in updates.items():
        setattr(meter, field, value)
    db.flush()
    response = MeterResponse.model_validate(meter)  # before commit (F-035)
    db.commit()
    return response


@router.delete("/{meter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meter(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="delete")
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if meter.replaced_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete a replaced meter")
    db.delete(meter)
    db.commit()
