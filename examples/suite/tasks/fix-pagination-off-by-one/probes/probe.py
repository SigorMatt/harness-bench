"""Observation probe for the pagination task.

Runs in the grading sandbox next to the patched code and only reports what
that code does, as JSON on stdout. Expected values never enter the sandbox:
the grader compares these observations outside it (see hb.grade).

Before the code under test is imported, the probe keeps a private copy of
stdout for its own report and points file descriptor 1 at stderr, so anything
the code prints goes to stderr and cannot corrupt the report.

Usage: python -I probe.py <tree-dir> <inputs.json>
"""
import importlib
import json
import os
import sys


def observe(fn, args):
    try:
        value = fn(*args)
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
    try:
        module = importlib.import_module(spec["module"])
    except BaseException as exc:
        observations = {"_import": {"error": type(exc).__name__}}
    else:
        observations = {}
        for case in spec["cases"]:
            fn = getattr(module, case["fn"], None)
            if fn is None:
                observations[case["id"]] = {"error": "MissingFunction"}
            else:
                observations[case["id"]] = observe(fn, case["args"])
    json.dump(observations, report, sort_keys=True)
    report.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
