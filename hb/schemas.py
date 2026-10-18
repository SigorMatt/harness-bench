"""Load the JSON Schemas in schemas/ and validate documents against them."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


@lru_cache(maxsize=1)
def _registry() -> tuple[Registry, dict]:
    by_name, resources = {}, []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        by_name[path.name.removesuffix(".schema.json")] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), by_name


def schema_names() -> list[str]:
    return sorted(_registry()[1])


def validator(name: str) -> Draft202012Validator:
    registry, by_name = _registry()
    return Draft202012Validator(by_name[name], registry=registry)


def validate(name: str, document) -> None:
    """Raise jsonschema.ValidationError if document does not match schema `name`."""
    validator(name).validate(document)
