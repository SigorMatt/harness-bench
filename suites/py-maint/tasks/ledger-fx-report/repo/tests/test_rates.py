from decimal import Decimal

import pytest

from ledgerkit import MissingRate, ParseError
from ledgerkit.fx import load_rates


def test_a_stated_rate():
    assert load_rates("EUR,USD,1.10\n").rate("EUR", "USD") == Decimal("1.10")


def test_a_rate_through_a_third_currency():
    rates = load_rates("# two rates\nEUR,USD,1.10\nUSD,JPY,150\n")
    assert rates.rate("EUR", "JPY") == Decimal("165.00")


def test_a_missing_rate():
    with pytest.raises(MissingRate):
        load_rates("EUR,USD,1.10\n").rate("EUR", "GBP")


def test_a_bad_rates_line():
    with pytest.raises(ParseError):
        load_rates("EUR,USD\n")
