import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from hb.schemas import SCHEMA_DIR, schema_names, validate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
BUNDLE = EXAMPLES / "run/task-bundles/fix-pagination-off-by-one"


@pytest.mark.parametrize("name", schema_names())
def test_schemas_are_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text()))


def load(path):
    return json.loads(Path(path).read_text())


@pytest.mark.parametrize("schema,path", [
    ("suite", EXAMPLES / "suite/suite.json"),
    ("task", EXAMPLES / "suite/tasks/fix-pagination-off-by-one/task.json"),
    ("registry", EXAMPLES / "registry.json"),
    ("task-base", EXAMPLES / "task-base.json"),
    ("harness", EXAMPLES.parent / "harnesses/baseline/harness.json"),
    ("prices", EXAMPLES / "prices.json"),
    ("cohort", EXAMPLES / "cohort.json"),
    ("entry", EXAMPLES / "entry.json"),
    ("release", EXAMPLES / "release.json"),
    ("run-manifest", EXAMPLES / "run/run-manifest.json"),
    ("task-manifest", BUNDLE / "task-manifest.json"),
] + [("lifecycle", p) for p in sorted(BUNDLE.glob("reps/*/lifecycle.json"))]
  + [("grade", p) for p in sorted(BUNDLE.glob("reps/*/grade.json"))])
def test_examples_match_their_schema(schema, path):
    validate(schema, load(path))


def test_every_gateway_record_matches_its_schema():
    lines = [line for p in sorted(BUNDLE.glob("reps/*/gateway.jsonl")) for line in p.read_text().splitlines()]
    assert lines
    for line in lines:
        validate("gateway-record", json.loads(line))


@pytest.mark.parametrize("schema,path,change", [
    ("cohort", EXAMPLES / "cohort.json", lambda d: d["runtime"].update(network="host")),
    ("cohort", EXAMPLES / "cohort.json", lambda d: d["runtime"].pop("task_base")),
    ("suite", EXAMPLES / "suite/suite.json", lambda d: d["runtime"].pop("task_base")),
    ("suite", EXAMPLES / "suite/suite.json", lambda d: d["runtime"].update(task_base="ghcr.io/x/y:latest")),
    ("task-base", EXAMPLES / "task-base.json", lambda d: d.update(image="ghcr.io/sigormatt/hb-toolkit@sha256:"
                                                                  + "1" * 64)),
    ("cohort", EXAMPLES / "cohort.json", lambda d: d["budget"].update(total_tokens=1.5)),
    ("entry", EXAMPLES / "entry.json", lambda d: d["harness"].update(image="ghcr.io/sigormatt/h:latest")),
    ("lifecycle", BUNDLE / "reps/1/lifecycle.json", lambda d: d.update(termination="infra_error")),
    ("task-manifest", BUNDLE / "task-manifest.json", lambda d: d["files"][0].update(path="../x")),
])
def test_schemas_reject_values_outside_the_protocol(schema, path, change):
    document = load(path)
    change(document)
    with pytest.raises(ValidationError):
        validate(schema, document)
