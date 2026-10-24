"""Observation probe for the python312-imports task.

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
def read_config(text):
    return module("plugboard").read_config(text)


def enabled(text):
    return module("plugboard").enabled_plugins(text)


def at_least(installed, required):
    return module("plugboard").version_at_least(installed, required)


def load_value(name, source, attribute):
    return getattr(module("plugboard").load_source(name, source), attribute)


def loaded_name(name, source):
    loaded = module("plugboard").load_source(name, source)
    return [loaded.__name__, name in sys.modules]


def registry_names(config, sources, host):
    return module("plugboard").load_plugins(config, sources, host_version=host).names()


def registry_call(config, sources, host, hook, args):
    return module("plugboard").load_plugins(config, sources, host_version=host).call(hook, *args)


def host_version():
    return module("plugboard").__version__


SCENARIOS = {"read_config": read_config, "enabled": enabled, "at_least": at_least, "load_value": load_value,
             "loaded_name": loaded_name, "registry_names": registry_names, "registry_call": registry_call,
             "host_version": host_version}


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
