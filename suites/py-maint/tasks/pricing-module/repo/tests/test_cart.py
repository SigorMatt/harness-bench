from decimal import Decimal

import pytest

from shopcart import Cart, LineItem


def test_total_applies_the_discount_before_tax():
    cart = Cart(tax_rate=Decimal("0.17"), discount_percent=Decimal("10"))
    cart.add("A", Decimal("19.99"), 2)
    cart.add("B", Decimal("5.00"))
    assert cart.subtotal() == Decimal("44.98")
    assert cart.total() == Decimal("47.36")


def test_adding_a_product_again_joins_its_line():
    cart = Cart()
    cart.add("A", Decimal("3"), 1)
    cart.add("A", Decimal("3.00"), 2)
    assert [(i.sku, i.quantity) for i in cart.items] == [("A", 3)]


def test_removing_a_missing_product():
    with pytest.raises(KeyError):
        Cart().remove("nope")


def test_quantity_must_be_positive():
    with pytest.raises(ValueError):
        LineItem("A", Decimal("1"), 0)
