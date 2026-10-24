"""One line of a transaction: an amount moved into (positive) or out of (negative) an account."""

from dataclasses import dataclass

from ..money import Money


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Money
    note: str = ""
