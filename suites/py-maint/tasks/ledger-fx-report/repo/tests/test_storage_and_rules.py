from ledgerkit import import_bank_csv, parse_journal
from ledgerkit.rules import PayeeContains, categorize
from ledgerkit.storage import from_json, to_json

JOURNAL = """
account Assets:Bank:EUR   EUR
account Expenses:Food     EUR

2024-03-01 Bakery
    Expenses:Food      4.50 EUR
    Assets:Bank:EUR   -4.50 EUR
"""


def test_json_round_trip():
    journal = parse_journal(JOURNAL)
    assert from_json(to_json(journal)).balances() == journal.balances()


def test_categorize_imported_rows():
    rows = "date,description,amount\n2024-03-02,BAKERY SUN,-3.20\n2024-03-03,Cinema,-12.00\n"
    imported = categorize(import_bank_csv(rows, "Assets:Bank:EUR", "EUR"), [(PayeeContains("bakery"), "Expenses:Food")])
    assert [t.postings[1].account for t in imported] == ["Expenses:Food", "Expenses:Unsorted"]
