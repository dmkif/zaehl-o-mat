import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Property, PropertyUser, PropertyUserRole, User
from app.security_log import log_event
from app.permissions import (
    accessible_property_ids,
    get_property_or_404,
    has_property_access,
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


def _ip(request: Request):
    return request.client.host if request.client else None


def _require_property_admin(
    db: Session, request: Request, event: str, property_id: uuid.UUID, user: User
) -> None:
    """Property edit / membership changes: property-admin (delete level) or global admin."""
    if not has_property_access(db, property_id, user, "delete"):
        log_event(event, outcome="denied", actor=user.id, target=property_id,
                  ip=_ip(request), reason="not_property_admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def _is_last_property_admin(db: Session, property_id: uuid.UUID, assoc: PropertyUser) -> bool:
    if assoc.role != PropertyUserRole.admin:
        return False
    admins = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.role == PropertyUserRole.admin)
        .count()
    )
    return admins <= 1


def _deny_self(request: Request, event: str, property_id: uuid.UUID, user: User) -> None:
    log_event(event, outcome="denied", actor=user.id, target=user.id, ip=_ip(request), reason="self_change")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot change your own property role")


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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prop = get_property_or_404(db, property_id)
    _require_property_admin(db, request, "property_update", property_id, current_user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(prop, field, value)
    db.commit()
    db.refresh(prop)
    log_event("property_update", actor=current_user.id, target=property_id, ip=_ip(request))
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    _require_property_admin(db, request, "member_changed", property_id, current_user)
    if body.user_id == current_user.id:
        _deny_self(request, "member_changed", property_id, current_user)
    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.user_id == body.user_id)
        .first()
    )
    if existing:
        if body.role != PropertyUserRole.admin and _is_last_property_admin(db, property_id, existing):
            log_event("member_changed", outcome="denied", actor=current_user.id,
                      target=body.user_id, ip=_ip(request), reason="last_property_admin")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot demote the last property admin")
        existing.role = body.role
        db.commit()
        db.refresh(existing)
        log_event("member_changed", actor=current_user.id, target=body.user_id, ip=_ip(request),
                  reason=f"role_{body.role.value}")
        return existing
    assoc = PropertyUser(property_id=property_id, user_id=body.user_id, role=body.role)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    log_event("member_added", actor=current_user.id, target=body.user_id, ip=_ip(request),
              reason=f"role_{body.role.value}")
    return assoc


@router.delete("/{property_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_property_user(
    property_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_property_or_404(db, property_id)
    _require_property_admin(db, request, "member_removed", property_id, current_user)
    if user_id == current_user.id:
        _deny_self(request, "member_removed", property_id, current_user)
    assoc = (
        db.query(PropertyUser)
        .filter(PropertyUser.property_id == property_id, PropertyUser.user_id == user_id)
        .first()
    )
    if not assoc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if _is_last_property_admin(db, property_id, assoc):
        log_event("member_removed", outcome="denied", actor=current_user.id, target=user_id,
                  ip=_ip(request), reason="last_property_admin")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove the last property admin")
    db.delete(assoc)
    db.commit()
    log_event("member_removed", actor=current_user.id, target=user_id, ip=_ip(request))
