"""SCORE_E8_V1: exact signed fixed-point boundary for serving contract v2."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite
from typing import Final

SCORE_E8_SCALE: Final = 100_000_000
SCORE_E8_MIN: Final = -(2**63)
SCORE_E8_MAX: Final = 2**63 - 1
_QUANTUM: Final = Decimal("0.00000001")
_RAW_MIN: Final = Decimal("-92233720368.54775808")
_RAW_MAX: Final = Decimal("92233720368.54775807")


class ScoreE8Error(ValueError):
    """A score cannot be represented under the frozen v2 serving contract."""


def score_e8_from_binary64(raw: float) -> int:
    """Freeze the legacy round-then-Decimal boundary without storing binary64."""

    if type(raw) is not float or not isfinite(raw):
        raise ScoreE8Error("score_e8_non_finite_or_invalid")
    exact_raw = Decimal.from_float(raw)
    if not _RAW_MIN <= exact_raw <= _RAW_MAX:
        raise ScoreE8Error("score_e8_raw_out_of_range")
    legacy_rounded = round(raw, 8)
    decimal_score = Decimal(str(legacy_rounded)).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    units = int(decimal_score * SCORE_E8_SCALE)
    if not SCORE_E8_MIN <= units <= SCORE_E8_MAX:
        raise ScoreE8Error("score_e8_units_out_of_range")
    return units


def score_e8_decimal(units: int) -> Decimal:
    """Return an exact NUMERIC value for PostgreSQL, never a binary float."""

    if type(units) is not int or not SCORE_E8_MIN <= units <= SCORE_E8_MAX:
        raise ScoreE8Error("score_e8_units_out_of_range")
    return Decimal(units) / SCORE_E8_SCALE


def score_e8_json_number(units: int) -> str:
    """Render the exact JSON number token with at most eight fractional digits."""

    score_e8_decimal(units)
    if units == 0:
        return "0"
    sign = "-" if units < 0 else ""
    whole, fraction = divmod(abs(units), SCORE_E8_SCALE)
    if fraction == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{fraction:08d}".rstrip("0")
