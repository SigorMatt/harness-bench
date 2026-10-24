"""Importing a bank's CSV export as transactions."""

import csv
import io

from ..dates import parse_date
from ..errors import ParseError
from ..model import Posting, Transaction
from ..money import Money
from ..util.decimal_utils import to_decimal

COLUMNS = ("date", "description", "amount")


def import_bank_csv(text, account, currency, counter_account="Expenses:Unsorted"):
    """Return one Transaction per row of a CSV with the columns date, description and amount.

    A positive amount is money into account, taken from counter_account; a negative one the reverse.
    rules.categorize can then pick better counter accounts.
    """
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise ParseError(1, f"the header must be {','.join(COLUMNS)}")
    transactions = []
    for number, row in enumerate(reader, 2):
        try:
            day = parse_date(row["date"])
            amount = Money(to_decimal(row["amount"]), currency)
        except (ValueError, TypeError) as exc:
            raise ParseError(number, str(exc)) from None
        transactions.append(Transaction(day, row["description"].strip(), [
            Posting(account, amount),
            Posting(counter_account, -amount),
        ]))
    return transactions
