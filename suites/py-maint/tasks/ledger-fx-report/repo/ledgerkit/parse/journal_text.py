"""The plain-text journal format.

    ; comments start with a semicolon
    account Assets:Bank:EUR   EUR
    account Income:Salary     EUR

    2024-01-31 Salary  #work
        Assets:Bank:EUR     3000.00 EUR
        Income:Salary      -3000.00 EUR

An "account" line declares an account and its currency. A transaction is a
date and a payee, optionally followed by #tags, then indented postings: an
account, two spaces or more, and an amount.
"""

from ..dates import parse_date
from ..errors import LedgerError, ParseError
from ..model import Journal, Posting, Transaction
from ..money import Money
from .tokens import is_indented, parse_amount, split_posting, strip_comment


def parse_journal(text):
    """Return the Journal that text describes. Raises ParseError, naming the line, for what it cannot read."""
    journal = Journal()
    pending = None  # (line number, Transaction) whose postings are being read

    def finish():
        if pending is not None:
            number, transaction = pending
            try:
                journal.add_transaction(transaction)
            except (LedgerError, ValueError) as exc:
                raise ParseError(number, str(exc)) from None

    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if not line.strip():
            continue
        if is_indented(line):
            if pending is None:
                raise ParseError(number, "a posting outside a transaction")
            parts = split_posting(line)
            if parts is None:
                raise ParseError(number, "expected an account, two spaces and an amount")
            try:
                amount, currency = parse_amount(parts[1])
                pending[1].postings.append(Posting(parts[0], Money(amount, currency)))
            except ValueError as exc:
                raise ParseError(number, str(exc)) from None
            continue
        finish()
        pending = None
        words = line.split()
        if words[0] == "account":
            if len(words) != 3:
                raise ParseError(number, "expected: account NAME CURRENCY")
            try:
                journal.add_account(words[1], words[2])
            except ValueError as exc:
                raise ParseError(number, str(exc)) from None
            continue
        try:
            day = parse_date(words[0])
        except ValueError:
            raise ParseError(number, f"expected a date or 'account', not {words[0]!r}") from None
        tags = tuple(word[1:] for word in words[1:] if word.startswith("#") and len(word) > 1)
        payee = " ".join(word for word in words[1:] if not word.startswith("#"))
        pending = (number, Transaction(day, payee, [], tags))
    finish()
    return journal
