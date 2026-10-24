from decimal import Decimal

import pytest

from ledgerkit import CurrencyMismatch, Money


def test_adding_amounts_in_one_currency():
    assert Money("1.10", "EUR") + Money("2.25", "EUR") == Money(Decimal("3.35"), "EUR")


def test_adding_amounts_in_two_currencies_is_refused():
    with pytest.raises(CurrencyMismatch):
        Money("1", "EUR") + Money("1", "USD")


def test_rounding_follows_the_currency():
    assert str(Money("2.675", "EUR")) == "2.68 EUR"
    assert str(Money("1234.5", "JPY")) == "1235 JPY"
