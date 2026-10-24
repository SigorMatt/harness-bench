"""Picking counter accounts for imported transactions by rules."""

from dataclasses import replace

from ..model import Posting


def categorize(transactions, rules, unsorted="Expenses:Unsorted"):
    """Return the transactions with the first matching rule's account in place of unsorted.

    rules is a list of (condition, account name); a condition is called with the
    transaction. Transactions no rule matches keep their postings.
    """
    out = []
    for transaction in transactions:
        account = next((name for condition, name in rules if condition(transaction)), None)
        if account is None:
            out.append(transaction)
            continue
        postings = [Posting(account, p.amount, p.note) if p.account == unsorted else p for p in transaction.postings]
        out.append(replace(transaction, postings=postings))
    return out
