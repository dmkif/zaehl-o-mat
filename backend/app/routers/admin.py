"""
Admin router — user management.
Only superadmin and admin roles may access these endpoints.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import require_admin, revoke_sessions
from app.database import get_db
from app.models import User, UserRole
from app.security_log import log_event
from app.schemas.admin import UserResponse, UserRoleUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=List[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return db.query(User).all()


def _ip(request: Request):
    return request.client.host if request.client else None


def _guard_superadmin_target(
    db: Session, request: Request, event: str, actor: User, target: User, new_role: UserRole | None
) -> None:
    """Only a superadmin may change/delete a superadmin; the last one is untouchable."""
    if target.role != UserRole.superadmin or new_role == UserRole.superadmin:
        return
    if actor.role != UserRole.superadmin:
        log_event(event, outcome="denied", actor=actor.id, target=target.id, ip=_ip(request),
                  reason="superadmin_protected")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmin can modify a superadmin")
    if db.query(User).filter(User.role == UserRole.superadmin).count() <= 1:
        log_event(event, outcome="denied", actor=actor.id, target=target.id, ip=_ip(request),
                  reason="last_superadmin")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove the last superadmin")


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def set_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # Only superadmin may grant superadmin role
    if body.role == UserRole.superadmin and current_user.role != UserRole.superadmin:
        log_event("user_role_changed", outcome="denied", actor=current_user.id, target=user_id,
                  ip=_ip(request), reason="grant_superadmin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmin can grant superadmin role")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    _guard_superadmin_target(db, request, "user_role_changed", current_user, user, body.role)
    user.role = body.role
    db.commit()
    db.refresh(user)
    revoke_sessions(db, user, actor=current_user.id, ip=_ip(request), reason="role_changed")
    log_event("user_role_changed", actor=current_user.id, target=user.id, ip=_ip(request),
              reason=f"role_{body.role.value}")
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    _guard_superadmin_target(db, request, "user_deleted", current_user, user, None)
    db.delete(user)
    db.commit()
    log_event("user_deleted", actor=current_user.id, target=user_id, ip=_ip(request))
