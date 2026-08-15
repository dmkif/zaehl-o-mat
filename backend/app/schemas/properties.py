import uuid
from typing import Optional
from pydantic import BaseModel
from app.models import PropertyType, PropertyUserRole


class PropertyCreate(BaseModel):
    name: str
    address: Optional[str] = None
    property_type: PropertyType = PropertyType.residential


class PropertyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    property_type: Optional[PropertyType] = None


class PropertyResponse(BaseModel):
    id: uuid.UUID
    name: str
    address: Optional[str]
    property_type: Optional[PropertyType]

    model_config = {"from_attributes": True}


class PropertyUserAdd(BaseModel):
    user_id: uuid.UUID
    role: PropertyUserRole = PropertyUserRole.user


class PropertyUserResponse(BaseModel):
    property_id: uuid.UUID
    user_id: uuid.UUID
    role: PropertyUserRole

    model_config = {"from_attributes": True}
