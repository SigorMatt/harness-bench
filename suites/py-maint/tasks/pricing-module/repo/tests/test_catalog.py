from decimal import Decimal

import pytest

from shopcart import load_catalog


def test_load_catalog():
    products = load_catalog("sku,name,price\nA1,Mug,$7.50\n")
    assert products["A1"].name == "Mug"
    assert products["A1"].price == Decimal("7.50")


def test_the_header_row_is_required():
    with pytest.raises(ValueError):
        load_catalog("A1,Mug,7.50\n")
