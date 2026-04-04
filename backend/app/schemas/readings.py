import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel
from app.models import ReadingSource


class ReadingCreate(BaseModel):
    value: Decimal
    read_at: Optional[datetime] = None
    source: Optional[ReadingSource] = ReadingSource.manual
    image_path: Optional[str] = None
    serial_image_path: Optional[str] = None
    note: Optional[str] = None


class ReadingResponse(BaseModel):
    id: int
    meter_id: uuid.UUID
    value: Decimal
    read_at: datetime
    source: ReadingSource
    image_path: Optional[str]
    serial_image_path: Optional[str]
    note: Optional[str]

    model_config = {"from_attributes": True}


class ReadingUpdate(BaseModel):
    value: Optional[Decimal] = None
    read_at: Optional[datetime] = None
    note: Optional[str] = None
