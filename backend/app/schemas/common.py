"""Shared input validation helpers (F-023).

``SafeStr`` rejects NUL bytes (PostgreSQL cannot store them) and lone UTF-16
surrogates (they cannot be encoded to UTF-8, which breaks the database driver
and the JSON error body).  ``ReadingValue`` bounds a reading to what
``Numeric(12, 3)`` can hold.
"""
import re
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

_BAD_CHARS = re.compile("[\x00\ud800-\udfff]")
_QUANT = Decimal("0.001")
_MAX_ABS = Decimal(10) ** 9  # Numeric(12, 3): 9 integer digits


def check_text(value: str) -> str:
    if _BAD_CHARS.search(value):
        raise ValueError("contains NUL or invalid characters")
    return value


def str_with_limit(max_length: int):
    """Plain ``str`` of at most ``max_length`` characters without NUL/surrogates."""
    return Annotated[str, Field(max_length=max_length), AfterValidator(check_text)]


def _decimal_in_range(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("value must be a finite number")
    # Bound before quantize(): quantize raises InvalidOperation for huge exponents.
    if abs(value) >= _MAX_ABS:
        raise ValueError("value out of range")
    return value.quantize(_QUANT, rounding=ROUND_HALF_EVEN)


def _reject_huge_literal(value):
    # A 400-digit literal is converted by pydantic; refuse absurd input early.
    if isinstance(value, str) and len(value) > 40:
        raise ValueError("value out of range")
    return value


ReadingValue = Annotated[Decimal, BeforeValidator(_reject_huge_literal), AfterValidator(_decimal_in_range)]
