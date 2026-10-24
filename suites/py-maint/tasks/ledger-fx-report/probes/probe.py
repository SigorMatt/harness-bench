"""Observation probe for the ledger-fx-report task.

Runs in the grading sandbox next to the patched code and only reports what
that code does, as JSON on stdout. Expected values never enter the sandbox:
the grader compares these observations outside it (spec §7).

Before the code under test is imported, the probe keeps a private copy of
stdout for its own report and points file descriptor 1 at stderr, so anything
the code prints goes to stderr and cannot corrupt the report.

Usage: python -I probe.py <tree-dir> <inputs.json>
"""
import importlib
import json
import os
import sys


def module(name):
    return importlib.import_module(name)


# What each case does with the code under test. Its observation is what the
# scenario returns (JSON), or the name of the exception it raises.
from decimal import Decimal  # noqa: E402  (the standard library's, not the code under test)


def _journal(text):
    return module("ledgerkit").parse_journal(text)


def _rates(text):
    return module("ledgerkit.fx").load_rates(text)


def balance(journal, rates, currency):
    report = module("ledgerkit.reports").balance_report(_journal(journal), _rates(rates), currency)
    return {"rows": [[row.account, str(row.native), str(row.converted)] for row in report.rows],
            "total": str(report.total)}


def formatted_balance(journal, rates, currency):
    reports = module("ledgerkit.reports")
    return reports.format_balance(reports.balance_report(_journal(journal), _rates(rates), currency))


def rate(rates, source, target):
    return str(Decimal(_rates(rates).rate(source, target)).quantize(Decimal("0.000001")))


def convert(rates, amount, source, target):
    ledgerkit = module("ledgerkit")
    return str(ledgerkit.fx.convert(ledgerkit.Money(amount, source), target, _rates(rates)))


def income(journal, rates, currency):
    statement = module("ledgerkit.reports").income_statement(_journal(journal), _rates(rates), currency)
    return [str(statement.income), str(statement.expenses), str(statement.net)]


def register(journal, account):
    lines = module("ledgerkit.reports").register(_journal(journal), account)
    return [[str(line.date), line.payee, str(line.amount), str(line.balance)] for line in lines]


def balances_after_json(journal):
    storage = module("ledgerkit.storage")
    restored = storage.from_json(storage.to_json(_journal(journal)))
    return {name: str(money) for name, money in sorted(restored.balances().items())}


def imported(rows, account, currency):
    transactions = module("ledgerkit").import_bank_csv(rows, account, currency)
    return [[str(t.date), t.payee, [[p.account, str(p.amount)] for p in t.postings]] for t in transactions]


def money_sum(a, a_currency, b, b_currency):
    ledgerkit = module("ledgerkit")
    return str(ledgerkit.Money(a, a_currency) + ledgerkit.Money(b, b_currency))


SCENARIOS = {"balance": balance, "formatted_balance": formatted_balance, "rate": rate, "convert": convert,
             "income": income, "register": register, "balances_after_json": balances_after_json,
             "imported": imported, "money_sum": money_sum}


def observe(scenario, args):
    try:
        value = scenario(*args)
        json.dumps(value)
        return {"ok": value}
    except BaseException as exc:  # the exception type name is the observation
        return {"error": type(exc).__name__}


def main(tree, inputs_path):
    with open(inputs_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    report = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    sys.path.insert(0, tree)
    observations = {}
    for case in spec["cases"]:
        scenario = SCENARIOS.get(case["scenario"])
        if scenario is None:
            observations[case["id"]] = {"error": "UnknownScenario"}
        else:
            observations[case["id"]] = observe(scenario, case["args"])
    json.dump(observations, report, sort_keys=True)
    report.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
