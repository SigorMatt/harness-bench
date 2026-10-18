"""Grading: compare sandbox observations against expectations, outside the sandbox.

The probe runs next to the patched code and can only emit observations.
Expected values and the comparison stay in the trusted grader process, so the
code under test cannot change the verdict -- it can only produce outputs, and
producing the expected outputs for hidden inputs is the task itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .patch import apply_patch
from .tree import materialize, out_of_scope, restore_protected, tree_changes

GRADE_SCHEMA = "hb/grade/1"
MAX_OBSERVATION_BYTES = 1 << 20

# Probe runner contract: run_probe(program, tree, inputs, timeout_seconds) returns
# the probe's stdout bytes. Whatever the code under test does -- hang, crash,
# exit early, print -- the runner returns normally, with b"" on timeout, so the
# outcome is "every check fails", never an aborted grading. It raises only for
# infrastructure faults (for example, the sandbox could not be started); those
# fail the job, so the run cannot be sealed. The toolkit runs the probe in a
# sandbox container (no network, read-only tree, time limit).
ProbeRunner = Callable[[Path, Path, Path, int], bytes]


def strict_equal(a, b) -> bool:
    """Equality that also requires identical JSON types (so true != 1, 1 != 1.0)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def parse_observations(raw: bytes) -> dict:
    """Parse probe stdout. Anything malformed yields {} -- every check then fails."""
    if len(raw) > MAX_OBSERVATION_BYTES:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def compare(observations: dict, expected: dict) -> dict:
    """Return {'passed', 'total'} for the expected cases."""
    passed = sum(1 for case, want in expected.items() if case in observations and strict_equal(observations[case], want))
    return {"passed": passed, "total": len(expected)}


def diff_stats(patch_text: str) -> dict:
    """Count files and changed lines in a git-format diff.

    Lines are counted only inside hunks, so content that itself starts with
    '--' or '++' (YAML separators, RST underlines) is counted correctly.
    """
    files = lines_added = lines_removed = 0
    in_hunk = False
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            lines_added += 1
        elif in_hunk and line.startswith("-"):
            lines_removed += 1
    return {"files_changed": files, "lines_added": lines_added, "lines_removed": lines_removed}


def verdict(target: dict, preservation: dict, strict_scope: bool, out_of_scope_files: int) -> bool:
    if target["total"] == 0:
        return False
    complete = target["passed"] == target["total"] and preservation["passed"] == preservation["total"]
    return complete and not (strict_scope and out_of_scope_files > 0)


def run_probe_unsandboxed_for_tests(program: Path, tree: Path, inputs: Path, timeout_seconds: int) -> bytes:
    """Local probe runner for this repository's own example task and tests.

    Follows the runner contract above. Not for grading submissions: the toolkit
    runs probes in a sandbox container.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(program), str(tree), str(inputs)],
            capture_output=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return b""
    return proc.stdout


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def grade_rep(*, suite: dict, task_dir: Path, patch: bytes, rep: int, grader_image: str,
              workdir: Path, run_probe: ProbeRunner) -> dict:
    """Grade one rep from its patch. Same function in the run and at the aggregator."""
    task_dir, workdir = Path(task_dir).resolve(), Path(workdir).resolve()
    task = _load_json(task_dir / "task.json")
    workdir.mkdir(parents=True, exist_ok=False)
    ignore = suite["ignore_paths"]

    pristine = workdir / "pristine"
    materialize(task_dir / task["repo"], pristine, ignore)
    tree = apply_patch(pristine, patch, workdir / "apply", ignore)
    touched = tree_changes(pristine, tree)  # before restoring: edits to protected paths count as edits
    restored = restore_protected(tree, pristine, suite["protected_paths"])

    probe = task["probe"]
    raw = run_probe(task_dir / probe["program"], tree, task_dir / probe["inputs"], probe["timeout_seconds"])
    observations = parse_observations(raw)
    target = compare(observations, _load_json(task_dir / task["checks"]["target"]))
    preservation = compare(observations, _load_json(task_dir / task["checks"]["preservation"]))

    strict = task["scope"]["strict"]
    outside = len(out_of_scope(touched, task["scope"]["allowed_paths"]))
    return {
        "schema": GRADE_SCHEMA,
        "task_id": task["id"],
        "rep": rep,
        "grader_image": grader_image,
        "resolved": verdict(target, preservation, strict, outside),
        "target": target,
        "preservation": preservation,
        "scope": {"strict": strict, "out_of_scope_files": outside},
        "normalization": {"protected_restored": len(restored)},
        "diff": diff_stats(patch.decode("utf-8", errors="replace")),
    }
