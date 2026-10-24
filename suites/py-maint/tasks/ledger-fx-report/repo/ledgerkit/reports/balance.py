"""The balance report: what each account holds, in its own currency and in the report's."""

from dataclasses import dataclass

from ..fx import convert
from ..money import Money

BALANCE_KINDS = ("Assets", "Liabilities")


@dataclass(frozen=True)
class BalanceRow:
    account: str
    native: Money     # in the account's currency
    converted: Money  # in the report's currency


@dataclass(frozen=True)
class BalanceReport:
    currency: str
    rows: tuple
    total: Money


def balance_report(journal, rates, currency, as_of=None, kinds=BALANCE_KINDS):
    """Balances of the accounts of the given kinds, as of a date, converted to currency.

    Accounts with a zero balance are left out. Each balance is converted and
    rounded on its own; the total is the sum of the rounded amounts.
    """
    rows = []
    for name, native in sorted(journal.balances(as_of).items()):
        if journal.accounts[name].kind not in kinds or native.is_zero():
            continue
        rows.append(BalanceRow(name, native.rounded(), convert(native, currency, rates)))
    total = Money.zero(currency)
    for row in rows:
        total = total + row.converted
    return BalanceReport(currency, tuple(rows), total)
