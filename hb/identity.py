"""Cohort and entry keys, derived from the suite and the harness registry.

Both keys are projections of files that are pinned at a release, so the set
of valid (cohort, entry) pairs for a release is known before any run happens.
Acceptance only has to check membership.
"""

from __future__ import annotations

import copy

from .canonical import content_id

COHORT_SCHEMA = "hb/cohort/1"
ENTRY_SCHEMA = "hb/entry/1"
_COHORT_FIELDS = ("model", "runtime", "budget", "track", "reps")


def cohort_from_suite(suite: dict, suite_digest: str) -> dict:
    """Project suite.json plus the suite tree digest into a cohort key."""
    cohort = {
        "schema": COHORT_SCHEMA,
        "suite": {"id": suite["id"], "version": suite["version"], "digest": suite_digest},
    }
    for field in _COHORT_FIELDS:
        cohort[field] = copy.deepcopy(suite[field])
    return cohort


def entry_from_registry(registry: dict, harness_id: str) -> dict:
    """Return the entry key for `harness_id`. Unknown ids are an error, not a fallback."""
    ids = [h["id"] for h in registry["harnesses"]]
    if len(ids) != len(set(ids)):
        raise ValueError("registry has duplicate harness ids")
    for harness in registry["harnesses"]:
        if harness["id"] == harness_id:
            return {
                "schema": ENTRY_SCHEMA,
                "harness": {
                    "id": harness["id"],
                    "version": harness["version"],
                    "image": harness["image"],
                    "config_digest": harness["config_digest"],
                },
            }
    raise KeyError(f"harness id not in registry: {harness_id!r}")


def cohort_id(cohort: dict) -> str:
    return content_id(cohort)


def entry_id(entry: dict) -> str:
    return content_id(entry)
