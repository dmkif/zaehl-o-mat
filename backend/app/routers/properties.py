import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Property, PropertyUser, PropertyUserRole, User, UserRole
from app.schemas.properties import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    PropertyUserAdd,
    PropertyUserResponse,
)

router = APIRouter(prefix="/properties", tags=["properties"])


def _get_property_or_404(db: Session, property_id: uuid.UUID) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop


def _user_can_access(prop: Property, user: User, db: Session) -> bool:
    if user.role in (UserRole.superadmin, UserRole.admin):
        return True
    assoc = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == prop.id, PropertyUser.user_id == user.id)
        .first()
    )
    return assoc is not None


def _user_can_manage(prop: Property, user: User, db: Session) -> bool:
    """True if user may modify the property (admin, superadmin, or property-manager)."""
    if user.role in (UserRole.superadmin, UserRole.admin):
        return True
    assoc = (
        db.query(PropertyUser)
        .filter(
            PropertyUser.property_id == prop.id,
            PropertyUser.user_id == user.id,
            PropertyUser.role.in_([PropertyUserRole.admin, PropertyUserRole.manager]),
        )
        .first()
    )
    return assoc is not None


@router.get("/", response_model=List[PropertyResponse])
def list_properties(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role in (UserRole.superadmin, UserRole.admin):
        return db.query(Property).all()
    # Only properties the user is assigned to
    assocs = db.query(PropertyUser).filter(PropertyUser.user_id == current_user.id).all()
    ids = [a.property_id for a in assocs]
    return db.query(Property).filter(Property.id.in_(ids)).all()


@router.post("/", response_model=PropertyResponse, status_code=status.HTTP_201_CREATED)
def create_property(
    body: PropertyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    prop = Property(id=uuid.uuid4(), **body.model_dump())
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


@router.get("/{property_id}", response_model=PropertyResponse)
def get_property(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _get_property_or_404(db, property_id)
    if not _user_can_access(prop, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return prop


@router.patch("/{property_id}", response_model=PropertyResponse)
def update_property(
    property_id: uuid.UUID,
    body: PropertyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _get_property_or_404(db, property_id)
    if not _user_can_manage(prop, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(prop, field, value)
    db.commit()
    db.refresh(prop)
    return prop


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_property(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    prop = _get_property_or_404(db, property_id)
    db.delete(prop)
    db.commit()


# --- Property users ---

@router.get("/{property_id}/users", response_model=List[PropertyUserResponse])
def list_property_users(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _get_property_or_404(db, property_id)
    if not _user_can_access(prop, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return db.query(PropertyUser).filter(PropertyUser.property_id == property_id).all()


@router.post("/{property_id}/users", response_model=PropertyUserResponse, status_code=status.HTTP_201_CREATED)
def add_property_user(
    property_id: uuid.UUID,
    body: PropertyUserAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _get_property_or_404(db, property_id)
    if not _user_can_manage(prop, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.user_id == body.user_id)
        .first()
    )
    if existing:
        existing.role = body.role
        db.commit()
        db.refresh(existing)
        return existing
    assoc = PropertyUser(property_id=property_id, user_id=body.user_id, role=body.role)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return assoc


@router.delete("/{property_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_property_user(
    property_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = _get_property_or_404(db, property_id)
    if not _user_can_manage(prop, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    assoc = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.user_id == user_id)
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(assoc)
    db.commit()
