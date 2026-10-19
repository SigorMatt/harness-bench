"""Acceptance of a submitted run by the aggregator.

Allowed operations only: signature verification (gh attestation verify, run as
argument lists, never through a shell), equality and set comparison against the
release table and the submission, and recomputation. Nothing here searches for
evidence of misbehaviour.
"""

from __future__ import annotations

from .identity import cohort_id, entry_id

FIRST_ATTEMPT = 1
RUN_MANIFEST_FILE = "run-manifest/run-manifest.json"


def run_key(run: dict) -> tuple[str, int]:
    """Deduplication key. GitHub repository names are case-insensitive."""
    return run["repository"].lower(), run["run_id"]


def bundle_artifact_file(task_id: str) -> str:
    """Where a task's bundle lands when a run's artifacts are downloaded, each into a
    directory named after its artifact (`gh run download -D <dir>`, or the download
    action without merge-multiple). The seal job and acceptance read the same path."""
    return f"task-bundle-{task_id}/task-bundle-{task_id}.tar"


def expected_artifact_files(release: dict) -> list[str]:
    """Files a sealed run must provide, as laid out by `gh run download -D <dir>`.

    Derived from the release's task list, so a submission cannot add or drop names.
    """
    return [RUN_MANIFEST_FILE] + [bundle_artifact_file(t) for t in sorted(release["task_ids"])]


def attestation_verify_args(artifact: str, *, source_repo: str, release: dict) -> list[str]:
    """Command that verifies an artifact was signed by the official workflow at the
    release commit, on a GitHub-hosted runner, in the submitter's repository."""
    return [
        "gh", "attestation", "verify", artifact,
        "--repo", source_repo,
        "--signer-workflow", release["signer_workflow"],
        "--signer-digest", release["signer_sha"],
        "--deny-self-hosted-runners",
    ]


def attestation_commands(run_dir: str, *, source_repo: str, release: dict) -> list[list[str]]:
    """One verify command per expected file, to be run with subprocess (no shell)."""
    return [
        attestation_verify_args(f"{run_dir}/{rel}", source_repo=source_repo, release=release)
        for rel in expected_artifact_files(release)
    ]


def check_run(run_manifest: dict, task_manifests: dict, bundle_digests: dict, release: dict,
              submission: dict) -> list[str]:
    """Return the reasons a verified run is not acceptable (empty list = accept).

    task_manifests: task_id -> parsed task-manifest.json (already verified against its bundle)
    bundle_digests: task_id -> sha256 of the task bundle archive as downloaded
    submission:     {"repository", "run_id"} the run was submitted as
    """
    errors = []
    cohort, entry, run = run_manifest["cohort"], run_manifest["entry"], run_manifest["run"]

    if run_key(run) != run_key(submission):
        errors.append("run manifest names a different repository or run than the submission")
    if run["run_attempt"] != FIRST_ATTEMPT:
        errors.append("only first attempts are accepted (no re-run jobs)")
    if run_manifest["release"] != {"toolkit_image": release["toolkit_image"], "suite_image": release["suite_image"]}:
        errors.append("release images differ from the release table")
    if cohort_id(cohort) not in release["cohort_ids"]:
        errors.append("cohort is not one of this release's cohorts")
    if entry_id(entry) not in release["entry_ids"]:
        errors.append("entry is not in this release's registry")

    listed = [t["task_id"] for t in run_manifest["tasks"]]
    if listed != sorted(release["task_ids"]):
        errors.append("task list differs from the suite (incomplete, unsorted or extra tasks)")
    if set(bundle_digests) != set(listed):
        errors.append("bundles present differ from the task list")

    for task in run_manifest["tasks"]:
        task_id = task["task_id"]
        if bundle_digests.get(task_id) != task["bundle"]["sha256"]:
            errors.append(f"{task_id}: bundle digest differs from the run manifest")
        manifest = task_manifests.get(task_id)
        if manifest is None:
            errors.append(f"{task_id}: task manifest missing")
            continue
        if manifest["run"] != run:
            errors.append(f"{task_id}: bundle comes from a different run")
        if manifest["task_id"] != task_id:
            errors.append(f"{task_id}: bundle is for another task")
        if manifest["reps"] != cohort["reps"]:
            errors.append(f"{task_id}: rep count differs from the cohort")
    return errors


def rerun_allowance(reps_in_run: int, max_rerun_permille: int) -> int:
    """How many infra re-runs (attempts - 1, summed over all reps) a run of reps_in_run reps may have.

    The task jobs stop at it as well (hb.orchestrator.run_task), so the one
    rule is written once.
    """
    return max_rerun_permille * reps_in_run // 1000


def rerun_budget_ok(attempts: list[int], max_rerun_permille: int) -> bool:
    """True when infra re-runs (sum of attempts - 1) stay within the suite's cap."""
    return sum(a - 1 for a in attempts) <= rerun_allowance(len(attempts), max_rerun_permille)


def check_attempts(attempts: list[int], infra: dict) -> list[str]:
    """Check every rep's attempt count against the suite's infra rules."""
    errors = []
    limit = 1 + infra["max_reruns_per_rep"]
    if any(a > limit for a in attempts):
        errors.append(f"a rep took more than {limit} attempts")
    if not rerun_budget_ok(attempts, infra["max_rerun_permille"]):
        errors.append("infra re-runs exceed the suite's cap")
    return errors
