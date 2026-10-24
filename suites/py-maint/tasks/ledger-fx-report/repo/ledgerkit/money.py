"""Amounts of money: a Decimal and a currency."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .currency import check_code, quantum
from .errors import CurrencyMismatch


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        check_code(self.currency)
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))

    @classmethod
    def zero(cls, currency):
        return cls(Decimal(0), currency)

    def _same(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} and {other.currency}")
        return other

    def __add__(self, other):
        other = self._same(other)
        if other is NotImplemented:
            return other
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other):
        other = self._same(other)
        if other is NotImplemented:
            return other
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self):
        return Money(-self.amount, self.currency)

    def is_zero(self):
        return self.amount == 0

    def rounded(self):
        """This amount rounded to its currency's minor unit, halves away from zero."""
        return Money(self.amount.quantize(quantum(self.currency), rounding=ROUND_HALF_UP), self.currency)

    def __str__(self):
        return f"{self.rounded().amount} {self.currency}"
