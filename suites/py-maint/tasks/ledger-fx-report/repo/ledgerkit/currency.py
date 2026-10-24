"""Currency codes and how many decimal places each one has."""

import re
from decimal import Decimal

_CODE = re.compile(r"[A-Z]{3}")

# ISO 4217 minor units of the currencies ledgerkit knows; others have two.
MINOR_UNITS = {"CHF": 2, "EUR": 2, "GBP": 2, "ILS": 2, "JPY": 0, "KWD": 3, "USD": 2}


def check_code(code):
    """Return code if it looks like an ISO 4217 code (three capital letters), else raise ValueError."""
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ValueError(f"not a currency code: {code!r}")
    return code


def minor_units(code):
    return MINOR_UNITS.get(check_code(code), 2)


def quantum(code):
    """The smallest amount of code that is written down: Decimal("0.01") for EUR, Decimal("1") for JPY."""
    return Decimal(1).scaleb(-minor_units(code))
