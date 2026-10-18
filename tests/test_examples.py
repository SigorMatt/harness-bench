"""The examples are generated, so they must regenerate identically, and their
grades must be reproducible from the stored patches alone (which is exactly
what the aggregator does)."""

import json
import subprocess
import sys
from pathlib import Path

from hb.grade import grade_rep, run_probe_unsandboxed_for_tests
from hb.manifest import pack_bundle, verify_task_bundle

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SUITE = json.loads((EXAMPLES / "suite/suite.json").read_text())
TASK_ID = "fix-pagination-off-by-one"
BUNDLE = EXAMPLES / "run/task-bundles" / TASK_ID


def test_examples_are_up_to_date():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/make_examples.py"), "--check"],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_example_bundle_matches_its_manifest_and_run_digest(tmp_path):
    verify_task_bundle(BUNDLE, json.loads((BUNDLE / "task-manifest.json").read_text()))
    run_manifest = json.loads((EXAMPLES / "run/run-manifest.json").read_text())
    [task] = run_manifest["tasks"]
    assert pack_bundle(BUNDLE, tmp_path / "bundle.tar") == task["bundle"]["sha256"]


def test_grades_are_reproducible_from_patches(tmp_path):
    for rep_dir in sorted((BUNDLE / "reps").iterdir()):
        stored = json.loads((rep_dir / "grade.json").read_text())
        regraded = grade_rep(
            suite=SUITE, task_dir=EXAMPLES / "suite/tasks" / TASK_ID,
            patch=(rep_dir / "workspace.patch").read_bytes(), rep=stored["rep"],
            grader_image=stored["grader_image"], workdir=tmp_path / rep_dir.name,
            run_probe=run_probe_unsandboxed_for_tests)
        assert regraded == stored, rep_dir.name
