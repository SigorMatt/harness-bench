"""The register: every posting to an account, with the running balance."""

from dataclasses import dataclass

from ..money import Money


@dataclass(frozen=True)
class RegisterLine:
    date: object
    payee: str
    amount: Money
    balance: Money


def register(journal, account, start=None, end=None):
    """The postings to account (and its subaccounts), in date order, with the balance after each.

    The balance starts from what the account held before start.
    """
    currency = journal.accounts[account].currency if account in journal.accounts else None
    lines, balance = [], None
    for transaction, posting in journal.postings(account):
        if balance is None:
            balance = Money.zero(currency or posting.amount.currency)
        balance = balance + posting.amount
        if (start is None or transaction.date >= start) and (end is None or transaction.date <= end):
            lines.append(RegisterLine(transaction.date, transaction.payee, posting.amount, balance))
    return lines
