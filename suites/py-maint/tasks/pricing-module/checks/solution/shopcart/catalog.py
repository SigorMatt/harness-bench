"""Products, read from CSV text with the columns sku, name and price."""

import csv
import io
from dataclasses import dataclass
from decimal import Decimal

from .money import parse_money


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal


def load_catalog(text):
    """Return {sku: Product} for CSV text whose header row is sku,name,price."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["sku", "name", "price"]:
        raise ValueError("the header row must be sku,name,price")
    products = {}
    for row in reader:
        sku = row["sku"].strip()
        if sku in products:
            raise ValueError(f"sku {sku} appears twice")
        products[sku] = Product(sku, row["name"].strip(), parse_money(row["price"]))
    return products
