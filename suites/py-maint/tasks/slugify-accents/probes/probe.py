"""Observation probe for the slugify-accents task.

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
def slugify(text, options):
    return module("textkit").slugify(text, **options)


SCENARIOS = {"slugify": slugify}


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
