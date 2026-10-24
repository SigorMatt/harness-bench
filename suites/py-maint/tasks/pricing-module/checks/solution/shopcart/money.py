"""Money arithmetic in Decimal: rounding to cents, discounts, tax and reading amounts."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def round_money(amount):
    """Round amount (a Decimal, an int or a numeric string) to cents, halves away from zero."""
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def apply_discount(amount, percent):
    """Return amount less percent per cent, rounded to cents. percent is from 0 to 100."""
    percent = Decimal(str(percent))
    if not 0 <= percent <= 100:
        raise ValueError("a discount is from 0 to 100 per cent")
    return round_money(Decimal(str(amount)) * (100 - percent) / 100)


def add_tax(amount, rate):
    """Return amount plus tax at rate (0.17 for 17%), rounded to cents."""
    rate = Decimal(str(rate))
    if rate < 0:
        raise ValueError("a tax rate cannot be negative")
    return round_money(Decimal(str(amount)) * (1 + rate))


def parse_money(text):
    """Read an amount such as "12.50", "$12.50" or "1,299.00" as a Decimal rounded to cents."""
    cleaned = text.strip().lstrip("$").replace(",", "")
    try:
        return round_money(Decimal(cleaned))
    except InvalidOperation:
        raise ValueError(f"not an amount of money: {text!r}") from None
