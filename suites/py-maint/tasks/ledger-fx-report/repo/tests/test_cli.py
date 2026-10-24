import io

from ledgerkit.cli import main


def test_balance_command(tmp_path):
    journal = tmp_path / "journal.txt"
    journal.write_text("account Assets:Bank  EUR\naccount Equity:Opening  EUR\n"
                       "2024-01-01 Open\n  Assets:Bank  10.00 EUR\n  Equity:Opening  -10.00 EUR\n")
    out = io.StringIO()
    assert main(["balance", str(journal), "--currency", "EUR"], out=out) == 0
    assert out.getvalue().splitlines()[-1].split() == ["Total", "10.00", "EUR"]


def test_a_missing_file(tmp_path):
    assert main(["balance", str(tmp_path / "none.txt"), "--currency", "EUR"], out=io.StringIO()) == 1
