"""ledgerkit on the command line.

    python -m ledgerkit balance journal.txt --rates rates.csv --currency EUR
    python -m ledgerkit income journal.txt --rates rates.csv --currency EUR --from 2024-01-01 --to 2024-12-31
    python -m ledgerkit register journal.txt Assets:Bank:EUR
"""

import argparse
import sys

from .dates import parse_date
from .errors import LedgerError
from .fx import RateTable, load_rates
from .parse import parse_journal
from .reports import (balance_report, format_balance, format_income, format_register, income_statement,
                      register)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="ledgerkit")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("balance", "income"):
        command = commands.add_parser(name)
        command.add_argument("journal")
        command.add_argument("--rates")
        command.add_argument("--currency", required=True)
        if name == "income":
            command.add_argument("--from", dest="start", type=parse_date)
            command.add_argument("--to", dest="end", type=parse_date)
        else:
            command.add_argument("--as-of", dest="as_of", type=parse_date)
    command = commands.add_parser("register")
    command.add_argument("journal")
    command.add_argument("account")
    args = parser.parse_args(argv)
    try:
        journal = parse_journal(_read(args.journal))
        if args.command == "register":
            out.write(format_register(register(journal, args.account)))
            return 0
        rates = load_rates(_read(args.rates)) if args.rates else RateTable()
        if args.command == "balance":
            out.write(format_balance(balance_report(journal, rates, args.currency, args.as_of)))
        else:
            out.write(format_income(income_statement(journal, rates, args.currency, args.start, args.end)))
    except (OSError, LedgerError) as exc:
        print(f"ledgerkit: {exc}", file=sys.stderr)
        return 1
    return 0
