import uuid
from typing import Optional
from pydantic import BaseModel
from app.models import UserRole


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class UserRoleUpdate(BaseModel):
    role: UserRole


class LdapMappingCreate(BaseModel):
    ldap_group: str
    app_role: UserRole


class LdapMappingResponse(BaseModel):
    id: int
    ldap_group: str
    app_role: UserRole

    model_config = {"from_attributes": True}
