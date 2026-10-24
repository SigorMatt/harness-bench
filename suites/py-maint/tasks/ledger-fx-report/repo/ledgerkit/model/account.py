"""Accounts: a colon-separated name, a currency, and a kind from the name's first part."""

import re
from dataclasses import dataclass

from ..currency import check_code

KINDS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")
_NAME = re.compile(r"[A-Z][A-Za-z0-9-]*(?::[A-Za-z0-9][A-Za-z0-9 -]*)*")


@dataclass(frozen=True)
class Account:
    name: str
    currency: str

    def __post_init__(self):
        if not _NAME.fullmatch(self.name) or self.name.split(":")[0] not in KINDS:
            raise ValueError(f"not an account name: {self.name!r} (it starts with one of {', '.join(KINDS)})")
        check_code(self.currency)

    @property
    def kind(self):
        return self.name.split(":")[0]

    @property
    def parent(self):
        return self.name.rsplit(":", 1)[0] if ":" in self.name else None

    def is_under(self, prefix):
        """True if this account is prefix or below it: Assets:Bank:EUR is under Assets:Bank."""
        return self.name == prefix or self.name.startswith(prefix + ":")
