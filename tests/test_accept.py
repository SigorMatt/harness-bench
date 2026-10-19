import copy
import json
from pathlib import Path

import pytest

from hb.accept import (
    attestation_commands, attestation_verify_args, check_attempts, check_run, expected_artifact_files,
    rerun_allowance, rerun_budget_ok, run_key,
)
from hb.manifest import pack_bundle

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
BUNDLES = EXAMPLES / "run/task-bundles"
TASK = "fix-pagination-off-by-one"
SUBMISSION = {"repository": "example-user/hb-runs", "run_id": 18000000001}


def load(rel):
    return json.loads((EXAMPLES / rel).read_text())


@pytest.fixture
def submission(tmp_path):
    run_manifest = load("run/run-manifest.json")
    task_manifests, digests = {}, {}
    for task in run_manifest["tasks"]:
        task_id = task["task_id"]
        task_manifests[task_id] = load(f"run/task-bundles/{task_id}/task-manifest.json")
        digests[task_id] = pack_bundle(BUNDLES / task_id, tmp_path / f"{task_id}.tar")
    return run_manifest, task_manifests, digests, load("release.json"), dict(SUBMISSION)


def test_the_example_run_is_accepted(submission):
    assert check_run(*submission) == []


def test_repository_names_compare_case_insensitively(submission):
    *rest, sub = submission
    assert check_run(*rest, {"repository": "Example-User/HB-Runs", "run_id": sub["run_id"]}) == []


def mutated(submission, change):
    run_manifest, task_manifests, digests, release, sub = copy.deepcopy(submission)
    change(run_manifest, task_manifests, digests, release, sub)
    return check_run(run_manifest, task_manifests, digests, release, sub)


@pytest.mark.parametrize("change,reason", [
    (lambda r, t, d, rel, s: s.update(run_id=18000000002), "different repository or run"),
    (lambda r, t, d, rel, s: s.update(repository="someone-else/hb-runs"), "different repository or run"),
    (lambda r, t, d, rel, s: r["run"].update(run_attempt=2), "first attempts"),
    (lambda r, t, d, rel, s: r["entry"]["harness"].update(version="9.9.9"), "entry"),
    (lambda r, t, d, rel, s: r["cohort"]["budget"].update(total_tokens=10**9), "cohort"),
    (lambda r, t, d, rel, s: r["release"].update(toolkit_image=rel["suite_image"]), "release images"),
    (lambda r, t, d, rel, s: d.update({TASK: "sha256:" + "0" * 64}), "bundle digest"),
    (lambda r, t, d, rel, s: t[TASK]["run"].update(run_id=42), "different run"),
    (lambda r, t, d, rel, s: rel.update(task_ids=[TASK, "another-task"]), "task list"),
])
def test_each_deviation_is_reported(submission, change, reason):
    errors = mutated(submission, change)
    assert errors and any(reason in e for e in errors), errors


def test_dedup_key_ignores_case_and_attempt():
    assert run_key({"repository": "A/B", "run_id": 7, "run_attempt": 1}) == run_key({"repository": "a/b", "run_id": 7})


def test_verify_command_pins_signer_and_runner_type():
    release = load("release.json")
    args = attestation_verify_args("task-bundle-x.tar", source_repo="example-user/hb-runs", release=release)
    assert args[:4] == ["gh", "attestation", "verify", "task-bundle-x.tar"]
    pairs = dict(zip(args[4::2], args[5::2]))
    assert pairs["--signer-workflow"] == release["signer_workflow"]
    assert pairs["--signer-digest"] == release["signer_sha"]
    assert pairs["--repo"] == "example-user/hb-runs"
    assert args[-1] == "--deny-self-hosted-runners"


def test_verification_covers_exactly_the_expected_files():
    release = load("release.json")
    assert expected_artifact_files(release) == [
        "run-manifest/run-manifest.json",
        f"task-bundle-{TASK}/task-bundle-{TASK}.tar",
    ]
    commands = attestation_commands("/submission", source_repo="example-user/hb-runs", release=release)
    assert [c[3] for c in commands] == [f"/submission/{f}" for f in expected_artifact_files(release)]


def test_the_example_run_is_within_its_infra_rules():
    suite = load("suite/suite.json")
    attempts = [load(f"run/task-bundles/{TASK}/reps/{n}/lifecycle.json")["attempts"] for n in (1, 2, 3)]
    assert check_attempts(attempts, suite["infra"]) == []


@pytest.mark.parametrize("attempts,ok", [([1] * 20, True), ([2] + [1] * 19, True), ([2, 2] + [1] * 18, False)])
def test_rerun_cap(attempts, ok):
    assert rerun_budget_ok(attempts, max_rerun_permille=50) is ok


@pytest.mark.parametrize("reps,permille,allowed", [(3, 50, 0), (19, 50, 0), (20, 50, 1), (768, 50, 38),
                                                   (5, 200, 1), (3, 0, 0)])
def test_rerun_allowance(reps, permille, allowed):
    assert rerun_allowance(reps, permille) == allowed


def test_the_allowance_is_exactly_the_cap_as_first_written():
    # rerun_budget_ok() is now written with rerun_allowance(), so both are checked
    # against the cap's original form: re-runs * 1000 <= permille * reps.
    for reps in range(1, 41):
        for permille in (0, 1, 33, 50, 199, 200):
            allowed = rerun_allowance(reps, permille)
            assert allowed * 1000 <= permille * reps < (allowed + 1) * 1000
            for reruns in range(reps + 1):
                attempts = [2] * reruns + [1] * (reps - reruns)
                assert rerun_budget_ok(attempts, permille) is (reruns * 1000 <= permille * reps)


def test_attempts_per_rep_are_capped():
    infra = {"max_reruns_per_rep": 2, "max_rerun_permille": 1000}
    assert check_attempts([3, 1, 1], infra) == []
    assert any("more than 3 attempts" in e for e in check_attempts([4, 1, 1], infra))
