"""Small pieces of the journal syntax."""

import re

from ..util.decimal_utils import to_decimal

# An account name and an amount are separated by two spaces or more, or a tab.
_POSTING = re.compile(r"(?P<account>\S(?:.*?\S)?)(?:\s{2,}|\t+)(?P<amount>\S.*)")
_AMOUNT = re.compile(r"(?P<number>[-+]?[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?P<currency>[A-Z]{3})")


def strip_comment(line):
    """The line without a ; comment and without trailing spaces."""
    return line.split(";", 1)[0].rstrip()


def is_indented(line):
    return line[:1] in (" ", "\t")


def split_posting(text):
    """Split "Assets:Bank   12.50 EUR" into ("Assets:Bank", "12.50 EUR"); None if it is not that shape."""
    match = _POSTING.fullmatch(text.strip())
    return (match["account"], match["amount"]) if match else None


def parse_amount(text):
    """Read "1,234.50 EUR" as (Decimal("1234.50"), "EUR"); raise ValueError otherwise."""
    match = _AMOUNT.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not an amount: {text!r}")
    return to_decimal(match["number"]), match["currency"]
