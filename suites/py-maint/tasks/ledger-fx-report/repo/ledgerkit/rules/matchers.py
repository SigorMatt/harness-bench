"""Conditions on imported transactions."""

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PayeeContains:
    text: str

    def __call__(self, transaction):
        return self.text.casefold() in transaction.payee.casefold()


@dataclass(frozen=True)
class PayeeMatches:
    pattern: str

    def __call__(self, transaction):
        return re.search(self.pattern, transaction.payee) is not None


@dataclass(frozen=True)
class AmountAtLeast:
    """The first posting's amount, as a number, is at least minimum (negative for money out)."""

    minimum: Decimal

    def __call__(self, transaction):
        return transaction.postings[0].amount.amount >= Decimal(str(self.minimum))


def all_of(*conditions):
    return lambda transaction: all(condition(transaction) for condition in conditions)
