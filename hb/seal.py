"""Sealing a run: one run manifest for the bundles the task jobs produced.

The seal job runs only after every task job succeeded. seal_run() recomputes
the cohort and entry from the pinned suite and registry, takes exactly one
bundle per suite task at the path acceptance verifies, verifies each against
its own manifest, checks that it comes from this run and this task with the
suite's rep count, that every rep's lifecycle names its task, its rep and the
registry's harness image, and checks the infra re-runs against the suite's
limits. Then it returns the run manifest, with each bundle's digest and size,
for the workflow to write and attest.

Sealing adds no trust: other jobs in the caller's workflow can upload
artifacts too, so acceptance verifies each bundle's own attestation and
repeats the run-level checks (spec §8). A run that fails any check made here
is never sealed. Schema validation and re-grading happen only at acceptance;
the lifecycle checks are seal's own consistency checks, which acceptance gets
from the attestation of the pinned toolkit that wrote those records.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path

from .accept import FIRST_ATTEMPT, bundle_artifact_file, check_attempts
from .identity import cohort_from_suite, entry_from_registry
from .manifest import (
    MANIFEST_NAME, TASK_MANIFEST_SCHEMA, VerificationError, extract_bundle, tree_digest, verify_task_bundle,
)
from .plan import task_ids

RUN_MANIFEST_SCHEMA = "hb/run-manifest/1"


class SealError(Exception):
    """The run cannot be sealed. The seal job fails, so nothing is attested."""


def _sha256_open(handle) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(1 << 20):
        digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SealError(f"{path.name}: cannot be read as JSON ({exc})") from None


def check_bundle(archive, *, task_id: str, run: dict, reps: int, harness_image: str,
                 scratch: Path) -> list[int]:
    """Verify one task bundle and return each rep's attempt count. Raises SealError.

    archive is the bundle as an open binary file, at its start.
    """
    dest = Path(scratch) / task_id
    try:
        extract_bundle(archive, dest)  # regular files only, nothing outside dest
        manifest = _load_json(dest / MANIFEST_NAME)
        if not isinstance(manifest, dict) or manifest.get("schema") != TASK_MANIFEST_SCHEMA:
            raise SealError(f"{task_id}: the bundle has no task manifest")
        if manifest.get("run") != run:
            raise SealError(f"{task_id}: the bundle comes from another run: {manifest.get('run')}")
        if manifest.get("task_id") != task_id:
            raise SealError(f"{task_id}: the bundle is for task {manifest.get('task_id')!r}")
        if manifest.get("reps") != reps:
            raise SealError(f"{task_id}: the bundle has {manifest.get('reps')} reps, the suite {reps}")
        verify_task_bundle(dest, manifest)  # exactly the expected files, with the listed hashes
    except (VerificationError, tarfile.TarError, OSError, KeyError, TypeError) as exc:
        raise SealError(f"{task_id}: the bundle does not verify: {exc}") from None
    attempts = []
    for rep in range(1, reps + 1):
        lifecycle = _load_json(dest / "reps" / str(rep) / "lifecycle.json")
        if not isinstance(lifecycle, dict) or (lifecycle.get("task_id"), lifecycle.get("rep")) != (task_id, rep):
            raise SealError(f"{task_id}: rep {rep} has a lifecycle record for another rep")
        if lifecycle.get("harness_image") != harness_image:
            raise SealError(f"{task_id}: rep {rep} ran {lifecycle.get('harness_image')!r}, not the registry's image")
        count = lifecycle.get("attempts")
        if type(count) is not int or count < 1:
            raise SealError(f"{task_id}: rep {rep} has no valid attempt count")
        attempts.append(count)
    return attempts


def seal_run(*, suite_dir: Path, bundles_dir: Path, registry: dict, harness_id: str, run: dict,
             release: dict, scratch: Path) -> dict:
    """The run manifest for this run's bundles. Raises SealError if the run cannot be accepted."""
    if run["run_attempt"] != FIRST_ATTEMPT:
        raise SealError(f"this is attempt {run['run_attempt']} of the run, and only first attempts are "
                        "accepted (spec I10): start a new run instead of re-running jobs")
    suite_dir, bundles_dir = Path(suite_dir), Path(bundles_dir)
    suite = _load_json(suite_dir / "suite.json")
    try:
        entry = entry_from_registry(registry, harness_id)
    except (KeyError, ValueError) as exc:
        raise SealError(f"harness {harness_id!r}: {exc}") from None
    try:
        cohort = cohort_from_suite(suite, tree_digest(suite_dir))
    except (VerificationError, ValueError) as exc:  # a suite tree holds regular files only
        raise SealError(f"the suite cannot be digested: {exc}") from None
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=False)

    tasks, attempts = [], []
    for task_id in task_ids(suite):
        relative = bundle_artifact_file(task_id)
        try:
            fd = os.open(bundles_dir / relative, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise SealError(f"{task_id}: no bundle {relative}") from None
        except OSError as exc:  # a symlink, among others
            raise SealError(f"{task_id}: {relative} is not a regular file ({exc.strerror})") from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise SealError(f"{task_id}: {relative} is not a regular file")
        with os.fdopen(fd, "rb") as archive:
            digest = _sha256_open(archive)
            archive.seek(0)  # the bytes extracted are the bytes hashed
            attempts += check_bundle(archive, task_id=task_id, run=run, reps=suite["reps"],
                                     harness_image=entry["harness"]["image"], scratch=scratch)
        tasks.append({"task_id": task_id, "bundle": {"sha256": digest, "size": info.st_size}})
    problems = check_attempts(attempts, suite["infra"])
    if problems:
        raise SealError("; ".join(problems))
    return {
        "schema": RUN_MANIFEST_SCHEMA,
        "run": run,
        "release": release,
        "cohort": cohort,
        "entry": entry,
        "tasks": tasks,
    }
