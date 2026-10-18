import uuid
from typing import Optional
from pydantic import BaseModel, field_validator
from app.models import PropertyType, PropertyUserRole
from app.schemas.common import str_with_limit


class PropertyCreate(BaseModel):
    name: str_with_limit(255)
    address: Optional[str_with_limit(2000)] = None
    property_type: PropertyType = PropertyType.residential


class PropertyUpdate(BaseModel):
    name: Optional[str_with_limit(255)] = None
    address: Optional[str_with_limit(2000)] = None
    property_type: Optional[PropertyType] = None

    @field_validator("name", mode="before")
    @classmethod
    def _name_not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class PropertyResponse(BaseModel):
    id: uuid.UUID
    name: str
    address: Optional[str]
    property_type: Optional[PropertyType]
    # The caller's role in this property (global admins: "admin"). Informational for the UI; the server decides.
    my_role: Optional[PropertyUserRole] = None

    model_config = {"from_attributes": True}


class PropertyUserAdd(BaseModel):
    user_id: uuid.UUID
    role: PropertyUserRole = PropertyUserRole.user


class PropertyUserResponse(BaseModel):
    property_id: uuid.UUID
    user_id: uuid.UUID
    role: PropertyUserRole

    model_config = {"from_attributes": True}
