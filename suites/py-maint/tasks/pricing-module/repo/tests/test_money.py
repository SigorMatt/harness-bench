from decimal import Decimal

import pytest

from shopcart.core import add_tax, apply_discount, parse_money, round_money


def test_round_money_rounds_halves_away_from_zero():
    assert round_money("2.675") == Decimal("2.68")
    assert round_money("-2.675") == Decimal("-2.68")


def test_apply_discount():
    assert apply_discount("19.99", 15) == Decimal("16.99")


def test_add_tax():
    assert add_tax("100", "0.17") == Decimal("117.00")


def test_parse_money():
    assert parse_money("$1,299.00") == Decimal("1299.00")
    with pytest.raises(ValueError):
        parse_money("ten")
