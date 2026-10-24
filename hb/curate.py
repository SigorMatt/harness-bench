"""Curation: what a task must satisfy before a suite grades harnesses with it (spec §7).

For every task of a suite, curate_task() checks:

- task.json is the task in its directory with the suite's category for it, and
  its scope is strict exactly for the restraint category;
- the target and preservation checks are objects with no case in both, and the
  target has at least one case;
- the probe reports the same observations twice, on the pristine repository and
  on the reference solution;
- the pristine repository fails every target check and passes every
  preservation check, so the preservation expectations are what pristine does;
- the reference solution (checks/solution, laid over the pristine repository)
  changes at least one file, only within the task's allowed paths and none of
  the suite's protected paths;
- graded by hb.grade.grade_rep exactly as a rep is, from its workspace diff, the
  reference solution resolves the task.

run_probe is a probe runner (hb.grade.ProbeRunner). These checks run a suite's
own probes on its own repositories. `scripts/suite.py check` uses
hb.grade.run_probe_unsandboxed_for_tests, or with --toolkit the probe sandbox
itself (hb.orchestrator.DockerProbeRunner), which is what grading uses.
"""

from __future__ import annotations

import itertools
import json
import shutil
from pathlib import Path

from .grade import compare, grade_rep, parse_observations, strict_equal
from .patch import workspace_diff
from .tree import materialize, matcher, out_of_scope, tree_changes

CURATION_GRADER = "curation"  # grader_image recorded in the grades curation computes, never published


class _Unfit(Exception):
    """A task curation cannot go on with; its message is the problem."""


def _load(path: Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _Unfit(f"{what} cannot be read as JSON ({exc.__class__.__name__})") from None


def curate_task(suite: dict, suite_dir: Path, entry: dict, run_probe, workdir: Path) -> list[str]:
    """Problems with one task of the suite (entry is its item in suite.json's tasks); [] if none.

    workdir must not exist yet; everything curation writes goes there.
    """
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True)
    problems = []
    try:
        _curate(suite, Path(suite_dir).resolve(), entry, run_probe, workdir, problems)
    except _Unfit as exc:
        problems.append(str(exc))
    return problems


def _curate(suite, suite_dir, entry, run_probe, workdir, problems):
    task_id = entry["id"]
    task_dir = suite_dir / "tasks" / task_id
    task = _load(task_dir / "task.json", "task.json")
    if task["id"] != task_id:
        problems.append(f"task.json names {task['id']!r}, not its directory")
    if task["category"] != entry["category"]:
        problems.append(f"task.json's category {task['category']!r} is not suite.json's {entry['category']!r}")
    if task["scope"]["strict"] != (task["category"] == "restraint"):
        problems.append("the scope must be strict for the restraint category, and only for it")

    target = _load(task_dir / task["checks"]["target"], "checks/target.json")
    preservation = _load(task_dir / task["checks"]["preservation"], "checks/preservation.json")
    if not isinstance(target, dict) or not target:
        raise _Unfit("checks/target.json must be an object with at least one case")
    if not isinstance(preservation, dict):
        raise _Unfit("checks/preservation.json must be an object")
    both = sorted(set(target) & set(preservation))
    if both:
        problems.append(f"cases in both the target and the preservation checks: {', '.join(both)}")
    solution = task_dir / task["checks"]["solution"]
    if not solution.is_dir():
        raise _Unfit(f"{task['checks']['solution']} is not a directory in the task directory")

    ignore, protected = suite["ignore_paths"], suite["protected_paths"]
    pristine = workdir / "pristine"
    materialize(task_dir / task["repo"], pristine, ignore)
    probe = task["probe"]

    copies = itertools.count(1)

    def observe(tree: Path, name: str) -> dict:
        # Each run probes a copy of the tree of its own, so nothing a probe writes reaches the trees
        # curation compares and diffs, nor the other run (the probe sandbox mounts its tree read-only).
        runs = []
        for _ in range(2):
            copy = workdir / f"probed-{next(copies)}"
            shutil.copytree(tree, copy, symlinks=True)
            runs.append(parse_observations(run_probe(task_dir / probe["program"], copy, task_dir / probe["inputs"],
                                                     probe["timeout_seconds"])))
        if runs[0] != runs[1]:
            problems.append(f"the probe reports different observations on two runs over {name}")
        return runs[0]

    seen = observe(pristine, "the pristine repository")
    passing = sorted(case for case, want in target.items() if case in seen and strict_equal(seen[case], want))
    if passing:
        problems.append(f"the pristine repository already passes target cases: {', '.join(passing)}")
    if compare(seen, preservation)["passed"] != len(preservation):
        failing = sorted(case for case, want in preservation.items()
                         if case not in seen or not strict_equal(seen[case], want))
        problems.append(f"the pristine repository fails preservation cases: {', '.join(failing)}")

    solved = workdir / "solved"
    shutil.copytree(pristine, solved)
    shutil.copytree(solution, solved, dirs_exist_ok=True)
    changed = tree_changes(pristine, solved)
    if not changed:
        problems.append("the reference solution changes nothing")
    outside = out_of_scope(changed, task["scope"]["allowed_paths"])
    if outside:
        problems.append(f"the reference solution changes files outside the allowed paths: {', '.join(outside)}")
    is_protected = matcher(protected)
    touched = [path for path in changed if is_protected(path)]
    if touched:
        problems.append(f"the reference solution changes protected paths: {', '.join(touched)}")
    observe(solved, "the reference solution")

    patch = workspace_diff(pristine, solved, workdir / "diff", ignore)
    grade = grade_rep(suite=suite, task_dir=task_dir, patch=patch, rep=1, grader_image=CURATION_GRADER,
                      workdir=workdir / "grade", run_probe=run_probe)
    if not grade["resolved"]:
        problems.append(f"the reference solution, graded as a rep, is not resolved "
                        f"(target {grade['target']['passed']}/{grade['target']['total']}, "
                        f"preservation {grade['preservation']['passed']}/{grade['preservation']['total']}, "
                        f"{grade['scope']['out_of_scope_files']} files out of scope)")


def curate_suite(suite_dir: Path, run_probe, workdir: Path) -> dict[str, list[str]]:
    """{task id: problems} for every task of the suite in suite_dir. workdir must not exist yet."""
    suite_dir, workdir = Path(suite_dir).resolve(), Path(workdir).resolve()
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    workdir.mkdir(parents=True)
    return {entry["id"]: curate_task(suite, suite_dir, entry, run_probe, workdir / entry["id"])
            for entry in suite["tasks"]}
