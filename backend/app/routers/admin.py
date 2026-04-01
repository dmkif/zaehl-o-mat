"""
Admin router — user management, LDAP role mappings.
Only superadmin and admin roles may access these endpoints.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_admin, get_current_user
from app.database import get_db
from app.models import LdapRoleMapping, User, UserRole
from app.schemas.admin import (
    UserResponse,
    UserRoleUpdate,
    LdapMappingCreate,
    LdapMappingResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=List[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return db.query(User).all()


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def set_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only superadmin may grant superadmin role
    if body.role == UserRole.superadmin and current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmin can grant superadmin role")
    if current_user.role not in (UserRole.superadmin, UserRole.admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    user.role = body.role
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if user.role == UserRole.superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete superadmin")
    db.delete(user)
    db.commit()


# --- LDAP role mappings ---

@router.get("/ldap-mappings", response_model=List[LdapMappingResponse])
def list_ldap_mappings(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return db.query(LdapRoleMapping).all()


@router.post("/ldap-mappings", response_model=LdapMappingResponse, status_code=status.HTTP_201_CREATED)
def create_ldap_mapping(
    body: LdapMappingCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = db.query(LdapRoleMapping).filter(LdapRoleMapping.ldap_group == body.ldap_group).first()
    if existing:
        existing.app_role = body.app_role
        db.commit()
        db.refresh(existing)
        return existing
    mapping = LdapRoleMapping(ldap_group=body.ldap_group, app_role=body.app_role)
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.delete("/ldap-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ldap_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    mapping = db.query(LdapRoleMapping).filter(LdapRoleMapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(mapping)
    db.commit()
