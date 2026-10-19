"""Planning a run and sealing it. The example run is the reference: sealing its
bundle must give exactly the committed example run manifest."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from hb.accept import bundle_artifact_file
from hb.manifest import MANIFEST_NAME, build_task_manifest, pack_bundle
from hb.plan import PlanError, plan_run, task_ids
from hb.schemas import validate
from hb.seal import SealError, seal_run

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SUITE_DIR = EXAMPLES / "suite"
BUNDLE_DIR = EXAMPLES / "run" / "task-bundles" / "fix-pagination-off-by-one"
TASK_ID = "fix-pagination-off-by-one"
REGISTRY = json.loads((EXAMPLES / "registry.json").read_text())
EXPECTED = json.loads((EXAMPLES / "run" / "run-manifest.json").read_text())
RUN = EXPECTED["run"]
RELEASE = EXPECTED["release"]


# ------------------------------------------------------------------ plan


def copy_suite(tmp_path) -> Path:
    suite_dir = tmp_path / "suite"
    shutil.copytree(SUITE_DIR, suite_dir)
    return suite_dir


def edit_json(path: Path, change) -> None:
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value, indent=2))


def test_plan_of_the_example_suite():
    assert plan_run(SUITE_DIR) == {"tasks": [TASK_ID], "timeout_minutes": 226}


@pytest.mark.parametrize("tasks", [
    [],
    [{"id": "b", "category": "restraint"}, {"id": "a", "category": "restraint"}],  # unsorted
    [{"id": "a", "category": "restraint"}, {"id": "a", "category": "restraint"}],  # duplicate
    [{"id": "Bad_Id", "category": "restraint"}],
    [{"id": f"t{i:03d}", "category": "restraint"} for i in range(257)],
])
def test_task_lists_the_matrix_cannot_take(tasks):
    with pytest.raises(PlanError):
        task_ids({"tasks": tasks})


def test_plan_refuses_a_listed_task_that_is_missing_or_misnamed(tmp_path):
    suite_dir = copy_suite(tmp_path)
    edit_json(suite_dir / "tasks" / TASK_ID / "task.json", lambda t: t.update(id="other-task"))
    with pytest.raises(PlanError, match="does not name this task"):
        plan_run(suite_dir)
    shutil.rmtree(suite_dir / "tasks" / TASK_ID)
    with pytest.raises(PlanError, match="cannot read task.json"):
        plan_run(suite_dir)


def test_plan_refuses_a_task_without_a_valid_probe_timeout(tmp_path):
    suite_dir = copy_suite(tmp_path)
    edit_json(suite_dir / "tasks" / TASK_ID / "task.json", lambda t: t["probe"].update(timeout_seconds=0))
    with pytest.raises(PlanError, match="no valid timeout"):
        plan_run(suite_dir)


def test_plan_refuses_a_suite_that_cannot_fit_in_a_job(tmp_path):
    suite_dir = copy_suite(tmp_path)
    edit_json(suite_dir / "suite.json", lambda s: s["budget"].update(wall_seconds=3600))
    with pytest.raises(ValueError, match="minutes per task job"):
        plan_run(suite_dir)


# ------------------------------------------------------------------ seal


def bundle_path(bundles: Path, task_id: str = TASK_ID) -> Path:
    """Where the seal job finds a task's bundle: one directory per artifact."""
    path = bundles / bundle_artifact_file(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_bundle(bundles: Path, bundle_dir: Path = BUNDLE_DIR, task_id: str = TASK_ID) -> Path:
    path = bundle_path(bundles, task_id)
    pack_bundle(bundle_dir, path)
    return path


def edited_bundle(tmp_path, change, run=RUN, task_id=TASK_ID, reps=3, manifest_change=None) -> Path:
    """A copy of the example bundle, changed and re-manifested, so it still verifies."""
    bundle_dir = tmp_path / "edited"
    shutil.copytree(BUNDLE_DIR, bundle_dir)
    change(bundle_dir)
    (bundle_dir / MANIFEST_NAME).unlink()
    manifest = build_task_manifest(bundle_dir, run=run, task_id=task_id, reps=reps)
    if manifest_change:
        manifest_change(manifest)
    (bundle_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    return bundle_dir


def edit_lifecycle(which, **changes):
    return lambda d: edit_json(d / "reps" / str(which) / "lifecycle.json", lambda lc: lc.update(**changes))


def seal(tmp_path, suite_dir=SUITE_DIR, run=RUN, harness_id="baseline", scratch="scratch"):
    return seal_run(suite_dir=suite_dir, bundles_dir=tmp_path / "bundles", registry=REGISTRY,
                    harness_id=harness_id, run=run, release=RELEASE, scratch=tmp_path / scratch)


def test_sealing_the_example_run_gives_its_run_manifest(tmp_path):
    write_bundle(tmp_path / "bundles")
    (tmp_path / "bundles" / "unrelated.txt").write_text("other files are ignored")
    # Another artifact carrying a file under the bundle's name changes nothing: seal reads
    # the bundle from its own artifact's directory, as acceptance does.
    (tmp_path / "bundles" / "task-bundle-extra").mkdir()
    (tmp_path / "bundles" / "task-bundle-extra" / f"task-bundle-{TASK_ID}.tar").write_bytes(b"not this one")
    manifest = seal(tmp_path)
    assert manifest == EXPECTED
    validate("run-manifest", manifest)


def test_a_missing_bundle_is_refused(tmp_path):
    (tmp_path / "bundles").mkdir()
    with pytest.raises(SealError, match=f"no bundle task-bundle-{TASK_ID}/task-bundle-{TASK_ID}.tar"):
        seal(tmp_path)


def test_a_bundle_path_that_is_a_symlink_or_a_directory_is_refused(tmp_path):
    real = write_bundle(tmp_path / "elsewhere")
    os.symlink(real, bundle_path(tmp_path / "bundles"))
    with pytest.raises(SealError, match="is not a regular file"):
        seal(tmp_path)
    os.unlink(bundle_path(tmp_path / "bundles"))
    bundle_path(tmp_path / "bundles").mkdir()
    with pytest.raises(SealError, match="is not a regular file"):
        seal(tmp_path, scratch="scratch-2")


def test_only_a_first_attempt_is_sealed(tmp_path):
    write_bundle(tmp_path / "bundles")
    with pytest.raises(SealError, match="only first attempts"):
        seal(tmp_path, run={**RUN, "run_attempt": 2})


def test_a_bundle_from_another_run_is_refused(tmp_path):
    write_bundle(tmp_path / "bundles", edited_bundle(tmp_path, lambda d: None, run={**RUN, "run_id": 1}))
    with pytest.raises(SealError, match="another run"):
        seal(tmp_path)


def test_a_bundle_that_does_not_match_its_manifest_is_refused(tmp_path):
    bundle_dir = tmp_path / "tampered"
    shutil.copytree(BUNDLE_DIR, bundle_dir)
    (bundle_dir / "reps" / "1" / "harness.log").write_text("changed after the manifest was written\n")
    write_bundle(tmp_path / "bundles", bundle_dir)
    with pytest.raises(SealError, match="does not verify"):
        seal(tmp_path)


def test_a_bundle_with_a_non_regular_member_is_refused(tmp_path):
    with tarfile.open(bundle_path(tmp_path / "bundles"), "w") as tar:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(SealError, match="does not verify"):
        seal(tmp_path)


def test_a_corrupt_bundle_is_refused(tmp_path):
    bundle_path(tmp_path / "bundles").write_bytes(b"not a tar archive" * 100)
    with pytest.raises(SealError, match="does not verify"):
        seal(tmp_path)


def test_a_bundle_made_with_another_harness_image_is_refused(tmp_path):
    def other_image(d):
        edit_json(d / "reps" / "2" / "lifecycle.json",
                  lambda lc: lc.update(harness_image="ghcr.io/sigormatt/other@sha256:" + "1" * 64))
    write_bundle(tmp_path / "bundles", edited_bundle(tmp_path, other_image))
    with pytest.raises(SealError, match="not the registry's image"):
        seal(tmp_path)


def test_reruns_over_the_suites_cap_are_not_sealed(tmp_path):
    # One re-run among three reps is 333 permille; the example suite allows 50.
    write_bundle(tmp_path / "bundles",
                 edited_bundle(tmp_path, lambda d: edit_json(d / "reps" / "1" / "lifecycle.json",
                                                             lambda lc: lc.update(attempts=2))))
    with pytest.raises(SealError, match="exceed the suite's cap"):
        seal(tmp_path)


@pytest.mark.parametrize("kwargs,message", [
    ({"manifest_change": lambda m: m.update(schema="hb/task-manifest/2")}, "has no task manifest"),
    ({"task_id": "other-task"}, "the bundle is for task"),
    ({"change": lambda d: shutil.rmtree(d / "reps" / "3"), "reps": 2}, "has 2 reps, the suite 3"),
    ({"change": edit_lifecycle(2, rep=3)}, "lifecycle record for another rep"),
    ({"change": edit_lifecycle(1, task_id="other-task")}, "lifecycle record for another rep"),
    ({"change": edit_lifecycle(1, attempts=0)}, "no valid attempt count"),
    ({"change": edit_lifecycle(3, attempts="1")}, "no valid attempt count"),
])
def test_inconsistent_bundles_are_refused(tmp_path, kwargs, message):
    kwargs = {"change": lambda d: None, **kwargs}
    write_bundle(tmp_path / "bundles", edited_bundle(tmp_path, kwargs.pop("change"), **kwargs))
    with pytest.raises(SealError, match=message):
        seal(tmp_path)


def test_a_rep_over_its_own_attempt_limit_is_not_sealed(tmp_path):
    # A run-wide cap that allows it, so the per-rep limit (1 + max_reruns_per_rep) decides.
    suite_dir = copy_suite(tmp_path)
    edit_json(suite_dir / "suite.json", lambda s: s["infra"].update(max_rerun_permille=1000))
    write_bundle(tmp_path / "bundles", edited_bundle(tmp_path, edit_lifecycle(1, attempts=4)))
    with pytest.raises(SealError, match="more than 3 attempts"):
        seal(tmp_path, suite_dir=suite_dir)


def test_a_suite_that_cannot_be_digested_is_refused(tmp_path):
    suite_dir = copy_suite(tmp_path)
    os.symlink("/etc/hostname", suite_dir / "tasks" / TASK_ID / "link")
    write_bundle(tmp_path / "bundles")
    with pytest.raises(SealError, match="cannot be digested"):
        seal(tmp_path, suite_dir=suite_dir)


def test_an_unknown_harness_is_refused(tmp_path):
    write_bundle(tmp_path / "bundles")
    with pytest.raises(SealError, match="not in registry"):
        seal(tmp_path, harness_id="no-such-harness")


# ------------------------------------------------------------------ command line


def cli(args, env, tmp_path):
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT), **env}
    return subprocess.run([sys.executable, "-m", "hb", *args], capture_output=True, text=True, cwd=tmp_path,
                          env=full_env, check=False)


def test_plan_command_writes_the_matrix_and_the_timeout(tmp_path):
    proc = cli(["plan", "--suite", str(SUITE_DIR), "--out", str(tmp_path / "out"),
                "--registry", str(EXAMPLES / "registry.json")], {"HARNESS_ID": "baseline", "HB_RUN_ATTEMPT": "1"},
               tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "out" / "tasks.json").read_text() == f'["{TASK_ID}"]\n'
    assert (tmp_path / "out" / "timeout_minutes").read_text() == "226\n"


@pytest.mark.parametrize("env,message", [
    ({"HARNESS_ID": "nope", "HB_RUN_ATTEMPT": "1"}, "not in the registry"),
    ({"HARNESS_ID": "baseline"}, "HB_RUN_ATTEMPT is missing"),
    ({"HARNESS_ID": "baseline", "HB_RUN_ATTEMPT": "2"}, "only first attempts are accepted"),
])
def test_plan_command_refuses_bad_inputs(tmp_path, env, message):
    proc = cli(["plan", "--suite", str(SUITE_DIR), "--out", str(tmp_path / "out"),
                "--registry", str(EXAMPLES / "registry.json")], env, tmp_path)
    assert proc.returncode == 2 and message in proc.stderr
    assert not (tmp_path / "out").exists()


def test_plan_command_fails_on_a_suite_it_cannot_run(tmp_path):
    suite_dir = copy_suite(tmp_path)
    edit_json(suite_dir / "suite.json", lambda s: s["budget"].update(wall_seconds=3600))
    proc = cli(["plan", "--suite", str(suite_dir), "--out", str(tmp_path / "out"),
                "--registry", str(EXAMPLES / "registry.json")], {"HARNESS_ID": "baseline", "HB_RUN_ATTEMPT": "1"},
               tmp_path)
    assert proc.returncode == 1 and "minutes per task job" in proc.stderr


SEAL_ENV = {
    "HARNESS_ID": "baseline",
    "HB_REPOSITORY": RUN["repository"], "HB_RUN_ID": str(RUN["run_id"]), "HB_RUN_ATTEMPT": "1",
    "HB_TOOLKIT_IMAGE": RELEASE["toolkit_image"], "HB_SUITE_IMAGE": RELEASE["suite_image"],
}


def test_seal_command_writes_the_run_manifest(tmp_path):
    write_bundle(tmp_path / "bundles")
    proc = cli(["seal", "--suite", str(SUITE_DIR), "--bundles", str(tmp_path / "bundles"),
                "--out", str(tmp_path / "out"), "--registry", str(EXAMPLES / "registry.json")], SEAL_ENV, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "out" / "run-manifest.json").read_text() == (EXAMPLES / "run/run-manifest.json").read_text()


@pytest.mark.parametrize("change,status", [
    ({"HB_SUITE_IMAGE": "ghcr.io/sigormatt/hb-suite:latest"}, 2),  # not pinned by digest
    ({"HB_RUN_ID": "x"}, 2),
    ({"HB_RUN_ATTEMPT": "2"}, 2),  # well formed, but a re-run is never accepted
])
def test_seal_command_refuses_bad_inputs(tmp_path, change, status):
    write_bundle(tmp_path / "bundles")
    proc = cli(["seal", "--suite", str(SUITE_DIR), "--bundles", str(tmp_path / "bundles"),
                "--out", str(tmp_path / "out"), "--registry", str(EXAMPLES / "registry.json")],
               {**SEAL_ENV, **change}, tmp_path)
    assert proc.returncode == status, proc.stderr
    assert not (tmp_path / "out" / "run-manifest.json").exists()


def test_seal_command_refuses_an_existing_work_directory(tmp_path):
    write_bundle(tmp_path / "bundles")
    (tmp_path / "work").mkdir()
    proc = cli(["seal", "--suite", str(SUITE_DIR), "--bundles", str(tmp_path / "bundles"),
                "--out", str(tmp_path / "out"), "--registry", str(EXAMPLES / "registry.json"),
                "--work", str(tmp_path / "work")], SEAL_ENV, tmp_path)
    assert proc.returncode == 2 and "already exists" in proc.stderr


def test_seal_reads_nothing_but_regular_members(tmp_path):
    # A hard link member would make the extracted file point elsewhere; it is refused like any non-regular member.
    (tmp_path / "bundles").mkdir()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"{}"
        info = tarfile.TarInfo("reps/1/lifecycle.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(MANIFEST_NAME)
        link.type, link.linkname = tarfile.LNKTYPE, "reps/1/lifecycle.json"
        tar.addfile(link)
    bundle_path(tmp_path / "bundles").write_bytes(buf.getvalue())
    with pytest.raises(SealError, match="does not verify"):
        seal(tmp_path)
