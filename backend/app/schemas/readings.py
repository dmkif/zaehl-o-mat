import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator
from app.models import ReadingSource
from app.upload_registry import is_valid_stored_path


class ReadingCreate(BaseModel):
    value: Decimal
    read_at: Optional[datetime] = None
    source: Optional[ReadingSource] = ReadingSource.manual
    image_path: Optional[str] = None
    serial_image_path: Optional[str] = None
    image_hash: Optional[str] = None
    note: Optional[str] = None

    @field_validator("image_path", "serial_image_path")
    @classmethod
    def _stored_path_shape(cls, v: Optional[str]) -> Optional[str]:
        # Only values produced by scan/bulk-scan are accepted on input (F-004);
        # responses for legacy readings are not validated.
        if v is None or v == "":
            return None
        if not is_valid_stored_path(v):
            raise ValueError("invalid image path")
        return v


class ReadingResponse(BaseModel):
    id: int
    meter_id: uuid.UUID
    value: Decimal
    read_at: datetime
    source: ReadingSource
    image_path: Optional[str]
    serial_image_path: Optional[str]
    image_hash: Optional[str]
    note: Optional[str]

    model_config = {"from_attributes": True}


class ReadingUpdate(BaseModel):
    value: Optional[Decimal] = None
    read_at: Optional[datetime] = None
    note: Optional[str] = None
