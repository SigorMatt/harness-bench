"""Observation probe for the pricing-module task.

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


def money_call(name, args):
    return str(getattr(module("shopcart.money"), name)(*args))


def core_call(name, args):
    return str(getattr(module("shopcart.core"), name)(*args))


def package_call(name, args):
    return str(getattr(module("shopcart"), name)(*args))


def money_constant(name):
    return str(getattr(module("shopcart.money"), name))


def defined_in(name):
    return getattr(module("shopcart.money"), name).__module__


def same_in_core(name):
    return getattr(module("shopcart.core"), name) is getattr(module("shopcart.money"), name)


def same_in_package(name):
    return getattr(module("shopcart"), name) is getattr(module("shopcart.money"), name)


def _cart(items, tax="0", discount="0"):
    cart = module("shopcart").Cart(tax_rate=Decimal(tax), discount_percent=Decimal(discount))
    for sku, price, quantity in items:
        cart.add(sku, Decimal(price), quantity)
    return cart


def cart_total(items, tax, discount):
    cart = _cart(items, tax, discount)
    return [str(cart.subtotal()), str(cart.total())]


def cart_lines(items):
    return [[item.sku, str(item.unit_price), item.quantity] for item in _cart(items).items]


def cart_remove(items, sku):
    cart = _cart(items)
    cart.remove(sku)
    return [item.sku for item in cart.items]


def line_item(sku, price, quantity):
    item = module("shopcart").LineItem(sku, Decimal(price), quantity)
    return [item.sku, str(item.unit_price), item.quantity, str(item.total)]


def catalog(text):
    return {sku: [p.name, str(p.price)] for sku, p in module("shopcart").load_catalog(text).items()}


def receipt(items, tax, discount, names):
    return module("shopcart").format_receipt(_cart(items, tax, discount), names)


SCENARIOS = {"money_call": money_call, "money_constant": money_constant, "core_call": core_call,
             "package_call": package_call, "defined_in": defined_in, "same_in_core": same_in_core,
             "same_in_package": same_in_package,
             "cart_total": cart_total, "cart_lines": cart_lines, "cart_remove": cart_remove,
             "line_item": line_item, "catalog": catalog, "receipt": receipt}


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
