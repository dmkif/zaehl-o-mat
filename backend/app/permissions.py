"""
Central property/meter access checks.

All routers must use these helpers instead of re-implementing PropertyUser
lookups, so the role semantics stay consistent in one place:

  - Global superadmin/admin: full access everywhere.
  - Property-level admin:    read, write and delete within the property.
  - Property-level manager:  read and write (add readings/meters), NO delete.
  - Property-level user:     read only.
"""
import uuid
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Meter, Property, PropertyUser, PropertyUserRole, User, UserRole

AccessLevel = Literal["read", "write", "delete"]

_LEVEL_ROLES: dict[str, list[PropertyUserRole]] = {
    "read": [PropertyUserRole.admin, PropertyUserRole.manager, PropertyUserRole.user],
    "write": [PropertyUserRole.admin, PropertyUserRole.manager],
    "delete": [PropertyUserRole.admin],
}


def is_global_admin(user: User) -> bool:
    return user.role in (UserRole.superadmin, UserRole.admin)


def get_property_or_404(db: Session, property_id: uuid.UUID) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop


def has_property_access(
    db: Session, property_id: uuid.UUID, user: User, level: AccessLevel = "read"
) -> bool:
    if is_global_admin(user):
        return True
    assoc = (
        db.query(PropertyUser)
        .filter(
            PropertyUser.property_id == property_id,
            PropertyUser.user_id == user.id,
            PropertyUser.role.in_(_LEVEL_ROLES[level]),
        )
        .first()
    )
    return assoc is not None


def require_property_access(
    db: Session, property_id: uuid.UUID, user: User, level: AccessLevel = "read"
) -> None:
    if not has_property_access(db, property_id, user, level):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def get_meter_with_access(
    db: Session,
    property_id: uuid.UUID,
    meter_id: uuid.UUID,
    user: User,
    level: AccessLevel = "read",
) -> Meter:
    get_property_or_404(db, property_id)
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.property_id == property_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
    require_property_access(db, property_id, user, level)
    return meter


def accessible_property_ids(user: User):
    """Scalar subquery of property ids the user is assigned to (any role).

    Only meaningful for non-global-admin users; admins see everything.
    """
    return (
        select(PropertyUser.property_id)
        .where(PropertyUser.user_id == user.id)
        .scalar_subquery()
    )
