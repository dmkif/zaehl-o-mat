from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

import bcrypt
from authlib.jose import JsonWebToken, JoseError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)
_jwt = JsonWebToken([settings.jwt_algorithm])


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_access_token_expire_minutes
    )
    payload["exp"] = int(expire.timestamp())
    header = {"alg": settings.jwt_algorithm}
    return _jwt.encode(header, payload, settings.jwt_secret_key).decode()


def decode_access_token(token: str) -> dict:
    claims = _jwt.decode(token, settings.jwt_secret_key)
    claims.validate()
    return dict(claims)


def get_or_create_user_from_oidc(db: Session, oidc_data: dict) -> User:
    sub = oidc_data.get("sub")
    email = oidc_data.get("email", "")
    username = oidc_data.get("preferred_username") or email.split("@")[0]
    groups: list[str] = oidc_data.get(settings.oidc_groups_claim, [])

    # Map OIDC groups → app role (highest privilege wins)
    role = UserRole.user
    if settings.oidc_admin_group in groups:
        role = UserRole.admin
    elif settings.oidc_manager_group in groups:
        role = UserRole.manager

    user = db.query(User).filter(User.oidc_sub == sub).first()
    if user:
        user.role = role
        user.email = email
        db.commit()
        db.refresh(user)
        return user

    # First login — create user
    user = User(
        id=uuid.uuid4(),
        username=username,
        email=email,
        oidc_sub=sub,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JoseError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(*roles: UserRole):
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles and current_user.role != UserRole.superadmin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return checker


require_admin = require_role(UserRole.admin)
require_manager = require_role(UserRole.admin, UserRole.manager)
require_user = require_role(UserRole.admin, UserRole.manager, UserRole.user)
