import json
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models import IntegrationType, MeterType, MeterUnit
from app.schemas.common import str_with_limit

MAX_INTEGRATION_CONFIG_BYTES = 8192


def _check_config(v: Optional[dict]) -> Optional[dict]:
    """Object or null only, JSON-serialisable (no NaN), small (stored as ASCII-escaped JSON text)."""
    if v is None:
        return None
    try:
        text = json.dumps(v, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("integration_config must be a JSON object") from None
    if len(text.encode("ascii")) > MAX_INTEGRATION_CONFIG_BYTES:
        raise ValueError("integration_config too large")
    return v


class MeterCreate(BaseModel):
    meter_type: MeterType
    unit: MeterUnit
    name: str_with_limit(255)
    serial_number: Optional[str_with_limit(100)] = None
    location: Optional[str_with_limit(255)] = None
    notes: Optional[str_with_limit(5000)] = None
    integration_type: IntegrationType = IntegrationType.manual
    integration_config: Optional[dict[str, Any]] = None

    _config = field_validator("integration_config")(_check_config)


class MeterUpdate(BaseModel):
    name: Optional[str_with_limit(255)] = None
    serial_number: Optional[str_with_limit(100)] = None
    location: Optional[str_with_limit(255)] = None
    notes: Optional[str_with_limit(5000)] = None
    integration_type: Optional[IntegrationType] = None
    integration_config: Optional[dict[str, Any]] = None
    replaced_at: Optional[datetime] = None
    replaced_by_id: Optional[uuid.UUID] = None

    _config = field_validator("integration_config")(_check_config)

    @field_validator("name", "integration_type", mode="before")
    @classmethod
    def _not_null(cls, v):
        # NOT NULL columns: an explicit null would be a 500 at commit time.
        if v is None:
            raise ValueError("must not be null")
        return v


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

    @model_validator(mode="after")
    def _redact_integration_config(self) -> "MeterResponse":
        """Replace integration_config with a boolean presence indicator.

        The config may contain API keys or passwords — never expose them in API responses.
        Clients only need to know whether a config exists, not its contents.
        """
        if self.integration_config is not None:
            self.integration_config = True
        return self
