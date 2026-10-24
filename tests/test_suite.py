"""The suite in suites/py-maint, and the curation checks every task must pass (spec §7).

Curation runs each task's own probe on its own repository with the local probe
runner, and grades the reference solution with hb.grade.grade_rep exactly as a
rep is graded. The repositories' own tests are run with pytest, as an agent
would run them. tests/test_run_task_docker.py runs the probes in the sandbox.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hb.curate import curate_suite, curate_task
from hb.grade import run_probe_unsandboxed_for_tests, strict_equal
from hb.release import check_inputs
from hb.schemas import validate
from hb.taskbase import TASK_BASE_FILE, check_suite_task_base, read_task_base

ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = ROOT / "suites" / "py-maint"
SUITE = json.loads((SUITE_DIR / "suite.json").read_text())
EXAMPLE_SUITE = ROOT / "examples" / "suite"
CATEGORIES = json.loads((ROOT / "schemas" / "common.schema.json").read_text())["$defs"]["category"]["enum"]


def task(task_id: str) -> dict:
    return json.loads((SUITE_DIR / "tasks" / task_id / "task.json").read_text())


# ------------------------------------------------------------------ the suite

def test_the_suite_matches_its_schema_and_has_one_task_per_category():
    validate("suite", SUITE)
    assert sorted(t["category"] for t in SUITE["tasks"]) == sorted(CATEGORIES)
    for entry in SUITE["tasks"]:
        validate("task", task(entry["id"]))


def test_the_suite_pins_the_pinned_task_base():
    check_suite_task_base(SUITE, read_task_base(ROOT))


def test_the_suite_is_a_valid_release_input(tmp_path):
    # The registry is not in the repository yet; a stand-in built on the pinned task base takes its place.
    registry = json.loads((ROOT / "examples" / "registry.json").read_text())
    for entry in registry["harnesses"]:
        entry["task_base"] = read_task_base(ROOT)["image"]
    (tmp_path / "registry.json").write_text(json.dumps(registry))
    inputs = check_inputs(SUITE_DIR, tmp_path / "registry.json", ROOT / TASK_BASE_FILE)
    assert inputs["suite_id"] == "py-maint"


@pytest.mark.parametrize("task_id", [entry["id"] for entry in SUITE["tasks"]])
def test_every_task_passes_curation(tmp_path, task_id):
    entry = next(e for e in SUITE["tasks"] if e["id"] == task_id)
    assert curate_task(SUITE, SUITE_DIR, entry, run_probe_unsandboxed_for_tests, tmp_path / "curation") == []


def test_the_example_suite_passes_curation(tmp_path):
    assert curate_suite(EXAMPLE_SUITE, run_probe_unsandboxed_for_tests, tmp_path / "curation") == {
        "fix-pagination-off-by-one": []}


def run_pytest(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=tree,
                          capture_output=True, text=True, timeout=300, check=False)


def trees(tmp_path: Path, task_id: str) -> tuple[Path, Path]:
    task_dir = SUITE_DIR / "tasks" / task_id
    pristine, solved = tmp_path / "pristine", tmp_path / "solved"
    shutil.copytree(task_dir / "repo", pristine)
    shutil.copytree(pristine, solved)
    shutil.copytree(task_dir / "checks" / "solution", solved, dirs_exist_ok=True)
    return pristine, solved


@pytest.mark.parametrize("task_id", [entry["id"] for entry in SUITE["tasks"]])
def test_the_repositorys_own_tests_pass_with_the_reference_solution(tmp_path, task_id):
    _, solved = trees(tmp_path, task_id)
    proc = run_pytest(solved)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("task_id", [e["id"] for e in SUITE["tasks"]
                                     if e["category"] in ("fix_with_failing_test", "build_repair",
                                                          "implement_to_spec")])
def test_the_repositorys_own_tests_fail_without_it(tmp_path, task_id):
    pristine, _ = trees(tmp_path, task_id)
    proc = run_pytest(pristine)
    assert proc.returncode != 0
    instruction = (SUITE_DIR / "tasks" / task_id / "instruction.md").read_text()
    named = re.findall(r"`(tests/[\w/]+\.py::\w+)`", instruction)
    for node in named:  # the failing test the instruction names, by its pytest node id
        assert f"FAILED {node}" in proc.stdout, proc.stdout


@pytest.mark.skipif(shutil.which("python3.11") is None, reason="needs python3.11, the last Python plugboard ran on")
def test_the_build_repair_targets_are_what_pristine_did_before_it_broke(tmp_path):
    # Pristine cannot import on 3.12, so the task has no preservation cases; its target
    # is the pristine repository's behaviour on Python 3.11 (spec §7).
    task_dir = SUITE_DIR / "tasks" / "python312-imports"
    shutil.copytree(task_dir / "repo", tmp_path / "repo")
    proc = subprocess.run(["python3.11", "-I", "-B", str(task_dir / "probes" / "probe.py"), str(tmp_path / "repo"),
                           str(task_dir / "probes" / "inputs.json")], capture_output=True, timeout=60, check=False)
    seen, target = json.loads(proc.stdout), json.loads((task_dir / "checks" / "target.json").read_text())
    assert seen.keys() == target.keys()
    assert [case for case in target if not strict_equal(seen[case], target[case])] == []  # as grading compares
    assert json.loads((task_dir / "checks" / "preservation.json").read_text()) == {}


# ------------------------------------------------------------------ the curation checks

EXAMPLE_TASK = EXAMPLE_SUITE / "tasks" / "fix-pagination-off-by-one"


@pytest.fixture
def example(tmp_path) -> Path:
    """A copy of the example suite to break."""
    shutil.copytree(EXAMPLE_SUITE, tmp_path / "suite")
    return tmp_path / "suite"


def problems(suite_dir: Path, workdir: Path) -> list[str]:
    return curate_suite(suite_dir, run_probe_unsandboxed_for_tests, workdir)["fix-pagination-off-by-one"]


def edit_json(path: Path, change) -> None:
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))


TASK = Path("tasks/fix-pagination-off-by-one")


def noisy_when_fixed(probes: Path) -> None:
    """Wrap the probe so it adds a random observation once the code is fixed, and only then."""
    shutil.copy(probes / "probe.py", probes / "real_probe.py")
    (probes / "probe.py").write_text(
        "import json, random, subprocess, sys\n"
        f"real = {str(probes / 'real_probe.py')!r}\n"
        "out = subprocess.run([sys.executable, '-I', real, *sys.argv[1:]], capture_output=True).stdout\n"
        "observations = json.loads(out)\n"
        "if '(number - 1)' in open(sys.argv[1] + '/pager.py').read():\n"
        "    observations['noise'] = random.random()\n"
        "json.dump(observations, sys.stdout)\n")


@pytest.mark.parametrize("break_it,message", [
    (lambda s: edit_json(s / TASK / "task.json", lambda d: d.update(category="restraint")),
     "is not suite.json's"),
    (lambda s: edit_json(s / TASK / "task.json", lambda d: d["scope"].update(strict=True)),
     "strict for the restraint category, and only for it"),
    (lambda s: (s / TASK / "checks/target.json").write_text("{}"), "at least one case"),
    (lambda s: edit_json(s / TASK / "checks/preservation.json", lambda d: d.update(t1={"ok": [10, 20]})),
     "in both the target and the preservation checks: t1"),
    (lambda s: edit_json(s / TASK / "checks/target.json", lambda d: d.update(p1={"ok": 0})),
     "already passes target cases: p1"),
    (lambda s: edit_json(s / TASK / "checks/preservation.json", lambda d: d.update(p2={"ok": 4})),
     "fails preservation cases: p2"),
    (lambda s: (s / TASK / "probes/probe.py").write_text(
        "import json, random, sys\njson.dump({'p1': {'ok': random.random()}}, sys.stdout)\n"),
     "different observations on two runs"),
    (lambda s: shutil.copy(s / TASK / "repo/pager.py", s / TASK / "checks/solution/pager.py"),
     "changes nothing"),
    (lambda s: (s / TASK / "checks/solution/notes.txt").write_text("x\n"),
     "outside the allowed paths: notes.txt"),
    (lambda s: (s / TASK / "checks/solution/tests").mkdir() or (s / TASK / "checks/solution/tests/test_pager.py")
     .write_text("def test_nothing():\n    pass\n"), "changes protected paths: tests/test_pager.py"),
    (lambda s: (s / TASK / "checks/solution/pager.py").write_text(
        (s / TASK / "repo/pager.py").read_text().replace("start = number * size", "start = 0")),
     "the reference solution, graded as a rep, is not resolved"),
    (lambda s: (edit_json(s / "suite.json", lambda d: d["tasks"][0].update(category="restraint")),
                edit_json(s / TASK / "task.json", lambda d: d.update(category="restraint"))),
     "strict for the restraint category, and only for it"),
    (lambda s: edit_json(s / TASK / "task.json", lambda d: d.update(id="other-task")),
     "task.json names 'other-task', not its directory"),
    (lambda s: (s / TASK / "checks/preservation.json").write_text("[]"), "must be an object"),
    (lambda s: (s / TASK / "checks/target.json").write_text("{"), "cannot be read as JSON"),
    (lambda s: (s / TASK / "checks/target.json").unlink(), "checks/target.json cannot be read as JSON"),
    (lambda s: shutil.rmtree(s / TASK / "checks/solution"), "is not a directory in the task directory"),
    (lambda s: noisy_when_fixed(s / TASK / "probes"), "two runs over the reference solution"),
])
def test_curation_reports_a_task_that_breaks_a_rule(example, tmp_path, break_it, message):
    break_it(example)
    found = problems(example, tmp_path / "curation")
    assert any(message in problem for problem in found), found


def test_a_strict_scope_counts_against_the_solution_when_graded(example, tmp_path):
    # A restraint task whose reference solution strays outside its scope is not resolved either.
    edit_json(example / "suite.json", lambda d: d["tasks"][0].update(category="restraint"))
    edit_json(example / TASK / "task.json", lambda d: (d.update(category="restraint"),
                                                       d["scope"].update(strict=True)))
    (example / TASK / "checks/solution/README.md").write_text("notes\n")
    found = problems(example, tmp_path / "curation")
    assert any("outside the allowed paths: README.md" in p for p in found), found
    assert any("graded as a rep, is not resolved" in p for p in found), found


def test_what_a_probe_writes_into_its_tree_is_not_part_of_the_reference_solution(example, tmp_path):
    # The probe adds a line to a file in its tree on every run. As a restraint task, that file would be
    # out of scope in the graded diff. Curation probes copies, so the probe's file is never in it.
    edit_json(example / "suite.json", lambda d: d["tasks"][0].update(category="restraint"))
    edit_json(example / TASK / "task.json", lambda d: (d.update(category="restraint"),
                                                       d["scope"].update(strict=True)))
    probes = example / TASK / "probes"
    shutil.copy(probes / "probe.py", probes / "real_probe.py")
    (probes / "probe.py").write_text(
        "import subprocess, sys\n"
        "open(sys.argv[1] + '/probe-notes.txt', 'a').write('probed\\n')\n"
        f"real = {str(probes / 'real_probe.py')!r}\n"
        "sys.stdout.buffer.write(subprocess.run([sys.executable, '-I', real, *sys.argv[1:]],"
        " capture_output=True).stdout)\n")
    assert problems(example, tmp_path / "curation") == []


def test_the_check_command(tmp_path):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "suite.py"), "check", str(EXAMPLE_SUITE)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and "fix-pagination-off-by-one: ok" in proc.stdout, proc.stderr
    broken = tmp_path / "suite"
    shutil.copytree(EXAMPLE_SUITE, broken)
    (broken / TASK / "checks/solution/notes.txt").write_text("x\n")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "suite.py"), "check", str(broken)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 1 and "not fit to grade with" in proc.stdout
    edit_json(broken / "suite.json", lambda d: d.pop("budget"))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "suite.py"), "check", str(broken)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 1 and "budget" in proc.stderr


def test_the_check_command_reports_a_probe_sandbox_it_cannot_set_up(tmp_path):
    # No docker on PATH: the sandbox cannot be set up, which is reported as such, with no traceback.
    (tmp_path / "bin").mkdir()
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "suite.py"), "check", "--toolkit",
                           "hb-local/toolkit:never", str(EXAMPLE_SUITE)], capture_output=True, text=True,
                          check=False, env={**os.environ, "PATH": str(tmp_path / "bin")})
    assert proc.returncode == 1 and proc.stderr.splitlines()[-1].startswith("suite: cannot run docker"), proc.stderr
    assert proc.stdout == "" and "Traceback" not in proc.stderr
