import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel
from app.models import MeterType, MeterUnit, IntegrationType


class MeterCreate(BaseModel):
    meter_type: MeterType
    unit: MeterUnit
    name: str
    serial_number: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    integration_type: IntegrationType = IntegrationType.none
    integration_config: Optional[Any] = None


class MeterUpdate(BaseModel):
    name: Optional[str] = None
    serial_number: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    integration_type: Optional[IntegrationType] = None
    integration_config: Optional[Any] = None
    replaced_at: Optional[datetime] = None
    replaced_by_id: Optional[uuid.UUID] = None


class MeterResponse(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    meter_type: MeterType
    unit: MeterUnit
    name: str
    serial_number: Optional[str]
    location: Optional[str]
    notes: Optional[str]
    integration_type: IntegrationType
    integration_config: Optional[Any]
    replaced_at: Optional[datetime]
    replaced_by_id: Optional[uuid.UUID]

    model_config = {"from_attributes": True}
