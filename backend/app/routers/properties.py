import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Property, PropertyUser, User
from app.permissions import (
    accessible_property_ids,
    get_property_or_404,
    is_global_admin,
    require_property_access,
)
from app.schemas.properties import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    PropertyUserAdd,
    PropertyUserResponse,
)

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("/", response_model=List[PropertyResponse])
def list_properties(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if is_global_admin(current_user):
        return db.query(Property).all()
    # Only properties the user is assigned to
    return (
        db.query(Property)
        .filter(Property.id.in_(accessible_property_ids(current_user)))
        .all()
    )


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
    prop = get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="read")
    return prop


@router.patch("/{property_id}", response_model=PropertyResponse)
def update_property(
    property_id: uuid.UUID,
    body: PropertyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="write")
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
    prop = get_property_or_404(db, property_id)
    db.delete(prop)
    db.commit()


# --- Property users ---

@router.get("/{property_id}/users", response_model=List[PropertyUserResponse])
def list_property_users(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="read")
    return db.query(PropertyUser).filter(PropertyUser.property_id == property_id).all()


@router.post("/{property_id}/users", response_model=PropertyUserResponse, status_code=status.HTTP_201_CREATED)
def add_property_user(
    property_id: uuid.UUID,
    body: PropertyUserAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="write")
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
    get_property_or_404(db, property_id)
    require_property_access(db, property_id, current_user, level="write")
    assoc = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.user_id == user_id)
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(assoc)
    db.commit()
