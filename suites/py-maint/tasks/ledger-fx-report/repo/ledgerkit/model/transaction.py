"""Transactions: a date, a payee and postings that sum to zero in every currency."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from ..errors import UnbalancedTransaction


@dataclass
class Transaction:
    date: datetime.date
    payee: str
    postings: list = field(default_factory=list)
    tags: tuple = ()

    def totals(self):
        """{currency: sum of the postings in it}."""
        sums = {}
        for posting in self.postings:
            sums[posting.amount.currency] = sums.get(posting.amount.currency, Decimal(0)) + posting.amount.amount
        return sums

    def check_balanced(self):
        if len(self.postings) < 2:
            raise UnbalancedTransaction(f"{self.date} {self.payee}: a transaction needs two postings or more")
        off = {currency: total for currency, total in self.totals().items() if total != 0}
        if off:
            listed = ", ".join(f"{total} {currency}" for currency, total in sorted(off.items()))
            raise UnbalancedTransaction(f"{self.date} {self.payee}: off by {listed}")
        return self

    def accounts(self):
        return [posting.account for posting in self.postings]
