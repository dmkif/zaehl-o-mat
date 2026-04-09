import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Meter, Property, PropertyUser, PropertyUserRole, User, UserRole
from app.schemas.meters import MeterCreate, MeterUpdate, MeterResponse

router = APIRouter(prefix="/properties/{property_id}/meters", tags=["meters"])


def _property_or_404(db: Session, property_id: uuid.UUID) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop


def _check_read(prop: Property, user: User, db: Session):
    if user.role in (UserRole.superadmin, UserRole.admin):
        return
    assoc = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == prop.id, PropertyUser.user_id == user.id)
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _check_write(prop: Property, user: User, db: Session):
    if user.role in (UserRole.superadmin, UserRole.admin):
        return
    assoc = (
        db.query(PropertyUser)
        .filter(
            PropertyUser.property_id == prop.id,
            PropertyUser.user_id == user.id,
            PropertyUser.role.in_([PropertyUserRole.admin, PropertyUserRole.manager]),
        )
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.get("/", response_model=List[MeterResponse])
def list_meters(
    property_id: uuid.UUID,
    include_replaced: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _property_or_404(db, property_id)
    _check_read(prop, current_user, db)
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
    prop = _property_or_404(db, property_id)
    _check_write(prop, current_user, db)
    meter = Meter(id=uuid.uuid4(), property_id=property_id, **body.model_dump())
    db.add(meter)
    db.commit()
    db.refresh(meter)
    return meter


@router.get("/{meter_id}", response_model=MeterResponse)
def get_meter(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _property_or_404(db, property_id)
    _check_read(prop, current_user, db)
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
    prop = _property_or_404(db, property_id)
    _check_write(prop, current_user, db)
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if meter.replaced_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot update a replaced meter")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meter, field, value)
    db.commit()
    db.refresh(meter)
    return meter


@router.delete("/{meter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meter(
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _property_or_404(db, property_id)
    _check_write(prop, current_user, db)
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if meter.replaced_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete a replaced meter")
    db.delete(meter)
    db.commit()
