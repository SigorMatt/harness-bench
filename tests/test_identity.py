import copy
import json
from pathlib import Path

import pytest

from hb.identity import cohort_from_suite, cohort_id, entry_from_registry, entry_id
from hb.manifest import tree_digest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_cohort_is_derived_from_suite_and_tree_digest():
    suite = load("suite/suite.json")
    cohort = cohort_from_suite(suite, tree_digest(EXAMPLES / "suite"))
    assert cohort == load("cohort.json")


def test_any_change_to_the_suite_tree_changes_the_cohort(tmp_path):
    suite = load("suite/suite.json")
    base = cohort_id(cohort_from_suite(suite, tree_digest(EXAMPLES / "suite")))
    copy_dir = tmp_path / "suite"
    import shutil

    shutil.copytree(EXAMPLES / "suite", copy_dir)
    target = copy_dir / "tasks" / "fix-pagination-off-by-one" / "checks" / "target.json"
    target.write_text(target.read_text() + " ")
    assert cohort_id(cohort_from_suite(suite, tree_digest(copy_dir))) != base


def test_entry_is_derived_from_registry():
    assert entry_from_registry(load("registry.json"), "baseline") == load("entry.json")


def test_unknown_harness_is_an_error():
    with pytest.raises(KeyError):
        entry_from_registry(load("registry.json"), "not-registered")


def test_duplicate_registry_ids_are_an_error():
    registry = load("registry.json")
    registry["harnesses"].append(copy.deepcopy(registry["harnesses"][0]))
    with pytest.raises(ValueError):
        entry_from_registry(registry, "baseline")


def test_entry_ids_differ_per_harness():
    registry = load("registry.json")
    ids = {entry_id(entry_from_registry(registry, h["id"])) for h in registry["harnesses"]}
    assert len(ids) == len(registry["harnesses"])
