import pytest

from ledgerkit import Money, ParseError, parse_journal

JOURNAL = """
account Assets:Bank:EUR   EUR
account Income:Salary     EUR
account Expenses:Food     EUR

2024-01-31 Salary  #work
    Assets:Bank:EUR     3,000.00 EUR
    Income:Salary      -3,000.00 EUR

2024-02-02 Market  ; weekly shop
    Expenses:Food         45.20 EUR
    Assets:Bank:EUR      -45.20 EUR
"""


def test_parse_a_journal():
    journal = parse_journal(JOURNAL)
    assert sorted(journal.accounts) == ["Assets:Bank:EUR", "Expenses:Food", "Income:Salary"]
    assert [t.payee for t in journal.transactions] == ["Salary", "Market"]
    assert journal.transactions[0].tags == ("work",)
    assert journal.balances()["Assets:Bank:EUR"] == Money("2954.80", "EUR")


def test_an_unbalanced_transaction_names_its_line():
    text = "account Assets:A  EUR\naccount Assets:B  EUR\n2024-01-01 Oops\n  Assets:A  1 EUR\n  Assets:B  -2 EUR\n"
    with pytest.raises(ParseError) as info:
        parse_journal(text)
    assert info.value.line == 3


def test_a_posting_in_the_wrong_currency_is_refused():
    text = "account Assets:A  EUR\naccount Assets:B  USD\n2024-01-01 X\n  Assets:A  1 EUR\n  Assets:B  -1 EUR\n"
    with pytest.raises(ParseError):
        parse_journal(text)
