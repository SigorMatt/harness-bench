from ledgerkit import parse_journal
from ledgerkit.fx import RateTable, load_rates
from ledgerkit.reports import balance_report, format_balance

JOURNAL = """
account Assets:Bank:EUR   EUR
account Assets:Cash       EUR
account Equity:Opening    EUR

2024-01-01 Opening balances
    Assets:Bank:EUR    1000.00 EUR
    Assets:Cash          50.00 EUR
    Equity:Opening    -1050.00 EUR
"""


def test_balance_in_the_accounts_currency():
    report = balance_report(parse_journal(JOURNAL), RateTable(), "EUR")
    assert [(row.account, str(row.converted)) for row in report.rows] == [
        ("Assets:Bank:EUR", "1000.00 EUR"), ("Assets:Cash", "50.00 EUR")]
    assert str(report.total) == "1050.00 EUR"


def test_balance_in_another_currency():
    report = balance_report(parse_journal(JOURNAL), load_rates("EUR,USD,1.10\n"), "USD")
    assert str(report.total) == "1155.00 USD"


def test_format_balance():
    text = format_balance(balance_report(parse_journal(JOURNAL), RateTable(), "EUR"))
    assert text.splitlines()[0].split() == ["Account", "Balance", "In", "EUR"]
    assert text.splitlines()[-1].split() == ["Total", "1050.00", "EUR"]
