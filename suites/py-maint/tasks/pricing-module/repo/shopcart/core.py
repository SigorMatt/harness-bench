"""Carts, line items and the money arithmetic they need."""

from dataclasses import dataclass, field
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


@dataclass
class LineItem:
    """quantity units of one product at unit_price each."""

    sku: str
    unit_price: Decimal
    quantity: int = 1

    def __post_init__(self):
        if self.quantity < 1:
            raise ValueError("quantity must be at least 1")
        self.unit_price = round_money(self.unit_price)

    @property
    def total(self):
        return round_money(self.unit_price * self.quantity)


@dataclass
class Cart:
    """Line items, with one discount and one tax rate for the whole cart."""

    tax_rate: Decimal = Decimal("0")
    discount_percent: Decimal = Decimal("0")
    items: list = field(default_factory=list)

    def add(self, sku, unit_price, quantity=1):
        """Add quantity units; units of a product already in the cart at that price join its line."""
        for item in self.items:
            if item.sku == sku and item.unit_price == round_money(unit_price):
                item.quantity += quantity
                return item
        item = LineItem(sku, unit_price, quantity)
        self.items.append(item)
        return item

    def remove(self, sku):
        """Remove every line for sku. Raises KeyError if there is none."""
        kept = [item for item in self.items if item.sku != sku]
        if len(kept) == len(self.items):
            raise KeyError(sku)
        self.items = kept

    def subtotal(self):
        return round_money(sum((item.total for item in self.items), Decimal("0")))

    def total(self):
        """The subtotal less the discount, plus tax on what is left."""
        return add_tax(apply_discount(self.subtotal(), self.discount_percent), self.tax_rate)
