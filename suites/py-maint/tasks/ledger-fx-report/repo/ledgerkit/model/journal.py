"""A journal: the declared accounts and the transactions between them."""

from ..dates import in_period
from ..errors import CurrencyMismatch, UnknownAccount
from ..money import Money
from .account import Account


class Journal:
    def __init__(self):
        self.accounts = {}
        self.transactions = []

    def add_account(self, name, currency):
        account = Account(name, currency)
        if name in self.accounts and self.accounts[name] != account:
            raise ValueError(f"account {name} is declared twice, with different currencies")
        self.accounts[name] = account
        return account

    def add_transaction(self, transaction):
        """Add a balanced transaction whose postings are to declared accounts, in their currencies."""
        for posting in transaction.postings:
            account = self.accounts.get(posting.account)
            if account is None:
                raise UnknownAccount(posting.account)
            if posting.amount.currency != account.currency:
                raise CurrencyMismatch(f"{posting.account} holds {account.currency}, "
                                       f"not {posting.amount.currency}")
        transaction.check_balanced()
        self.transactions.append(transaction)
        self.transactions.sort(key=lambda t: t.date)
        return transaction

    def postings(self, account=None, start=None, end=None):
        """(transaction, posting) pairs in date order, for one account and its subaccounts if given."""
        for transaction in self.transactions:
            if not in_period(transaction.date, start, end):
                continue
            for posting in transaction.postings:
                if account is None or posting.account == account or posting.account.startswith(account + ":"):
                    yield transaction, posting

    def balances(self, as_of=None):
        """{account name: Money in the account's currency} after every transaction up to as_of."""
        totals = {name: Money.zero(account.currency) for name, account in self.accounts.items()}
        for _, posting in self.postings(end=as_of):
            totals[posting.account] = totals[posting.account] + posting.amount
        return totals
