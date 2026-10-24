"""Carts and line items."""

from dataclasses import dataclass, field
from decimal import Decimal

# The money helpers live in shopcart.money; they are imported here too, because
# existing code imports them from shopcart.core.
from .money import CENT, add_tax, apply_discount, parse_money, round_money  # noqa: F401


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
