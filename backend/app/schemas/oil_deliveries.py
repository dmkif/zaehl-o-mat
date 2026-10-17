import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import str_with_limit

MIN_DELIVERY_DATE = datetime(2000, 1, 1, tzinfo=timezone.utc)
MAX_LITERS = Decimal("100000")
MAX_TOTAL_PRICE = Decimal("10000000")
MAX_PRICE_PER_LITER = Decimal("1000")
_CENT = Decimal("0.01")
_PRICE_4 = Decimal("0.0001")


def derive_prices(
    liters: Decimal, total: Optional[Decimal], per_liter: Optional[Decimal]
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Fill in the missing price from the given one; reject inconsistent pairs.

    Both given: they may differ by at most 1 cent per 100 L (plus rounding of a cent).
    """
    if total is not None and per_liter is not None:
        if abs(total - liters * per_liter) > liters * Decimal("0.0001") + _CENT:
            raise ValueError("total_price and price_per_liter are inconsistent")
    elif total is not None:
        per_liter = (total / liters).quantize(_PRICE_4, ROUND_HALF_UP)
    elif per_liter is not None:
        total = (liters * per_liter).quantize(_CENT, ROUND_HALF_UP)
    if total is not None and total > MAX_TOTAL_PRICE or per_liter is not None and per_liter > MAX_PRICE_PER_LITER:
        raise ValueError("price out of range")
    return total, per_liter


class _DeliveryFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("delivered_at", check_fields=False)
    @classmethod
    def _date_in_range(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v < MIN_DELIVERY_DATE or v > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("delivered_at out of range")
        return v


def _liters(default=...):
    return Field(default, gt=0, le=MAX_LITERS, max_digits=10, decimal_places=2, allow_inf_nan=False)


def _total():
    return Field(None, ge=0, le=MAX_TOTAL_PRICE, max_digits=10, decimal_places=2, allow_inf_nan=False)


def _per_liter():
    return Field(None, ge=0, le=MAX_PRICE_PER_LITER, max_digits=8, decimal_places=4, allow_inf_nan=False)


class OilDeliveryCreate(_DeliveryFields):
    delivered_at: datetime
    liters: Decimal = _liters()
    total_price: Optional[Decimal] = _total()
    price_per_liter: Optional[Decimal] = _per_liter()
    supplier: Optional[str_with_limit(255)] = None
    note: Optional[str_with_limit(2000)] = None

    @model_validator(mode="after")
    def _derive(self):
        self.total_price, self.price_per_liter = derive_prices(self.liters, self.total_price, self.price_per_liter)
        return self


class OilDeliveryUpdate(_DeliveryFields):
    """Partial update: only fields that are sent change (explicit null clears a price/text)."""
    delivered_at: Optional[datetime] = None
    liters: Optional[Decimal] = _liters(None)
    total_price: Optional[Decimal] = _total()
    price_per_liter: Optional[Decimal] = _per_liter()
    supplier: Optional[str_with_limit(255)] = None
    note: Optional[str_with_limit(2000)] = None

    @model_validator(mode="after")
    def _required_not_null(self):
        for name in ("liters", "delivered_at"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class OilDeliveryResponse(BaseModel):
    id: int
    delivered_at: datetime
    liters: Decimal
    total_price: Optional[Decimal]
    price_per_liter: Optional[Decimal]
    supplier: Optional[str]
    note: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProbableDeliveryResponse(BaseModel):
    property_id: uuid.UUID
    meter_id: uuid.UUID
    from_reading_id: int
    to_reading_id: int
    from_at: datetime
    to_at: datetime
    estimated_liters: int


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_reading_id: int = Field(ge=1, le=2**63 - 1)
    to_reading_id: int = Field(ge=1, le=2**63 - 1)
