"""The income statement: income, expenses and what is left, over a period."""

from dataclasses import dataclass

from ..fx import convert_all
from ..money import Money


@dataclass(frozen=True)
class IncomeStatement:
    currency: str
    income: Money    # positive: income postings are negative, so their sign is flipped
    expenses: Money
    net: Money


def income_statement(journal, rates, currency, start=None, end=None):
    """Income and expenses between start and end (both included), converted to currency."""
    income, expenses = [], []
    for _, posting in journal.postings(start=start, end=end):
        kind = journal.accounts[posting.account].kind
        if kind == "Income":
            income.append(-posting.amount)
        elif kind == "Expenses":
            expenses.append(posting.amount)
    earned = convert_all(income, currency, rates)
    spent = convert_all(expenses, currency, rates)
    return IncomeStatement(currency, earned, spent, earned - spent)


def monthly(journal, rates, currency, year):
    """An IncomeStatement for each month of year, January first."""
    from ..dates import month_bounds

    return [income_statement(journal, rates, currency, *month_bounds(year, month)) for month in range(1, 13)]


def zero_statement(currency):
    return IncomeStatement(currency, Money.zero(currency), Money.zero(currency), Money.zero(currency))
