"""Rates files: one "BASE,QUOTE,RATE" line per rate; blank lines and # comments are skipped.

    # one EUR is worth 1.10 USD
    EUR,USD,1.10
    USD,JPY,150
"""

from ..errors import ParseError
from ..util.decimal_utils import to_decimal
from .rates import RateTable


def load_rates(text):
    """Return the RateTable a rates file's text states. Raises ParseError, naming the line."""
    table = RateTable()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3:
            raise ParseError(number, "expected BASE,QUOTE,RATE")
        try:
            table.set(fields[0], fields[1], to_decimal(fields[2]))
        except ValueError as exc:
            raise ParseError(number, str(exc)) from None
    return table
