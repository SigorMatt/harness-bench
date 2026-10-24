"""Decimal helpers."""

from decimal import Decimal, InvalidOperation


def to_decimal(text):
    """Read a number such as "1,234.50" or "-12" as a Decimal. Raises ValueError."""
    cleaned = str(text).strip().replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a finite number: {text!r}")
    return value


def sum_decimals(values):
    return sum(values, Decimal(0))
