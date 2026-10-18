import json
import shutil
from pathlib import Path

import pytest

from hb.grade import (
    compare, diff_stats, grade_rep, parse_observations, run_probe_unsandboxed_for_tests, strict_equal, verdict,
)
from hb.patch import workspace_diff
from hb.tree import materialize

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SUITE = json.loads((EXAMPLES / "suite/suite.json").read_text())
TASK_DIR = EXAMPLES / "suite/tasks/fix-pagination-off-by-one"
TOOLKIT = "ghcr.io/owner/hb-toolkit@sha256:" + "d" * 64


@pytest.mark.parametrize("a,b,expected", [
    (1, 1, True), (1, True, False), (1, 1.0, False), ([1, [2]], [1, [2]], True),
    ({"ok": [1]}, {"ok": [1]}, True), ({"ok": 1}, {"ok": 1, "x": 0}, False), ("1", 1, False),
])
def test_strict_equal(a, b, expected):
    assert strict_equal(a, b) is expected


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b"\xff\xfe", b"{" * 10, b" " * ((1 << 20) + 1)])
def test_malformed_observations_become_empty(raw):
    assert parse_observations(raw) == {}


def test_compare_counts_only_exact_matches():
    expected = {"a": {"ok": 1}, "b": {"ok": [2]}, "c": {"error": "ValueError"}}
    observed = {"a": {"ok": 1}, "b": {"ok": [2.0]}, "extra": {"ok": 0}}
    assert compare(observed, expected) == {"passed": 1, "total": 3}


def test_verdict_rules():
    full = {"passed": 3, "total": 3}
    assert verdict(full, {"passed": 2, "total": 2}, strict_scope=False, out_of_scope_files=4)
    assert not verdict(full, {"passed": 2, "total": 2}, strict_scope=True, out_of_scope_files=1)
    assert not verdict(full, {"passed": 1, "total": 2}, strict_scope=False, out_of_scope_files=0)
    assert not verdict({"passed": 0, "total": 0}, {"passed": 0, "total": 0}, False, 0)


def test_diff_stats_ignores_headers():
    patch = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n+more\n"
    assert diff_stats(patch) == {"files_changed": 1, "lines_added": 2, "lines_removed": 1}


def test_diff_stats_counts_content_that_looks_like_headers():
    patch = ("diff --git a/doc.rst b/doc.rst\n--- a/doc.rst\n+++ b/doc.rst\n@@ -1,2 +1,2 @@\n"
             " Title\n---- old underline\n+++++ new underline\n"
             "diff --git a/c.yml b/c.yml\n--- a/c.yml\n+++ b/c.yml\n@@ -0,0 +1 @@\n+---\n")
    assert diff_stats(patch) == {"files_changed": 2, "lines_added": 2, "lines_removed": 1}


def grade_tree(tmp_path, tree_from, task_dir=TASK_DIR):
    """Grade a workspace produced by copying the pristine repo and overlaying files."""
    pristine = tmp_path / "pristine"
    materialize(task_dir / "repo", pristine)
    workspace = tmp_path / "workspace"
    shutil.copytree(pristine, workspace)
    tree_from(workspace)
    patch = workspace_diff(pristine, workspace, tmp_path / "diff", SUITE["ignore_paths"])
    return grade_rep(suite=SUITE, task_dir=task_dir, patch=patch, rep=1, grader_image=TOOLKIT,
                     workdir=tmp_path / "grade", run_probe=run_probe_unsandboxed_for_tests)


def replace_in(path, old, new):
    path.write_text(path.read_text().replace(old, new))


def test_pristine_repo_fails_target_and_passes_preservation(tmp_path):
    grade = grade_tree(tmp_path, lambda ws: None)
    assert grade["target"] == {"passed": 0, "total": 3}
    assert grade["preservation"] == {"passed": 5, "total": 5}
    assert grade["resolved"] is False


def test_reference_solution_resolves(tmp_path):
    def apply_solution(ws):
        shutil.copytree(TASK_DIR / "checks/solution", ws, dirs_exist_ok=True)

    grade = grade_tree(tmp_path, apply_solution)
    assert grade["resolved"] is True
    assert grade["target"] == {"passed": 3, "total": 3}


def test_editing_the_tests_cannot_make_a_rep_pass(tmp_path):
    def edit_only_tests(ws):
        (ws / "tests/test_pager.py").write_text("def test_nothing():\n    assert True\n")

    grade = grade_tree(tmp_path, edit_only_tests)
    assert grade["resolved"] is False
    assert grade["normalization"]["protected_restored"] == 1


def test_output_printed_by_the_code_under_test_does_not_corrupt_observations(tmp_path):
    def noisy_solution(ws):
        shutil.copytree(TASK_DIR / "checks/solution", ws, dirs_exist_ok=True)
        replace_in(ws / "pager.py", '    start = (number - 1) * size\n',
                   '    print("debug: computing start")\n    start = (number - 1) * size\n')
        (ws / "pager.py").write_text('print("loading pager")\n' + (ws / "pager.py").read_text())

    grade = grade_tree(tmp_path, noisy_solution)
    assert grade["resolved"] is True


def test_a_hanging_patch_fails_every_check_without_aborting(tmp_path):
    fast_task = tmp_path / "task"
    shutil.copytree(TASK_DIR, fast_task)
    task = json.loads((fast_task / "task.json").read_text())
    task["probe"]["timeout_seconds"] = 2
    (fast_task / "task.json").write_text(json.dumps(task))

    def hang(ws):
        replace_in(ws / "pager.py", "    start = number * size\n", "    while True:\n        pass\n")

    grade = grade_tree(tmp_path / "run", hang, task_dir=fast_task)
    assert grade["target"] == {"passed": 0, "total": 3}
    assert grade["resolved"] is False


def test_caches_left_in_the_workspace_are_not_edits(tmp_path):
    def solve_and_leave_caches(ws):
        shutil.copytree(TASK_DIR / "checks/solution", ws, dirs_exist_ok=True)
        (ws / "__pycache__").mkdir()
        (ws / "__pycache__/pager.cpython-313.pyc").write_bytes(b"\x00bytecode")
        (ws / ".pytest_cache").mkdir()
        (ws / ".pytest_cache/README.md").write_text("cache\n")

    grade = grade_tree(tmp_path, solve_and_leave_caches)
    assert grade["resolved"] is True
    assert grade["scope"]["out_of_scope_files"] == 0
    assert grade["diff"]["files_changed"] == 1
