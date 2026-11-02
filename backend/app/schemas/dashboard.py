"""Response models for the consumption statistics endpoints (feature 015)."""
from typing import Optional

from pydantic import BaseModel


class PreviousYear(BaseModel):
    value: Optional[float]
    state: str


class MonthValue(BaseModel):
    month: str  # YYYY-MM, Europe/Berlin calendar
    value: Optional[float]
    state: str  # complete | partial | no_data
    estimated: bool = False
    previous_year: Optional[PreviousYear] = None
    delta_pct: Optional[float] = None
    cost: Optional[float] = None


class MonthlySeries(BaseModel):
    meter_type: str
    unit: str
    months: list[MonthValue]


class Scope(BaseModel):
    kind: str  # meter | property | all
    id: Optional[str] = None


class MonthlyResponse(BaseModel):
    scope: Scope
    series: list[MonthlySeries]


class YearSeries(BaseModel):
    meter_type: str
    unit: str
    year_to_date: Optional[float]
    previous_year_same_period: Optional[float]
    delta_pct: Optional[float]
    cost: Optional[float]
    cost_complete: bool


class YearlyResponse(BaseModel):
    scope: Scope
    as_of: str
    series: list[YearSeries]
