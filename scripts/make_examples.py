#!/usr/bin/env python3
"""Regenerate the derived example files from the hand-written sources.

Sources:  examples/suite/ (suite.json + one task) and examples/registry.json.
Derived:  examples/cohort.json, entry.json, release.json and examples/run/
          (run-manifest.json plus one task bundle with three reps).

The rep patches are built with difflib so they are identical on every
machine; grades are computed by hb.grade.grade_rep, the same function the
aggregator uses. `--check` regenerates into a temporary directory and exits
non-zero if anything differs, so the examples cannot drift from the code.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.grade import grade_rep, run_probe_unsandboxed_for_tests  # noqa: E402
from hb.identity import cohort_from_suite, cohort_id, entry_from_registry, entry_id  # noqa: E402
from hb.manifest import MANIFEST_NAME, build_task_manifest, pack_bundle, tree_digest  # noqa: E402

EXAMPLES = ROOT / "examples"
DERIVED = ("cohort.json", "entry.json", "release.json", "run")

TOOLKIT_IMAGE = "ghcr.io/owner/hb-toolkit@sha256:de1c3876583de809d67dab8a38fc05fe7c669e7f511b6a383eca295cb8420abe"
SUITE_IMAGE = "ghcr.io/owner/hb-suite-py-maint@sha256:cd81d58c20a810329cb37daac6035921d3efc80c1cf0bf83e4bb2d95f110a9a2"
SIGNER_WORKFLOW = "OWNER/harnessbench/.github/workflows/bench.yml"
SIGNER_SHA = hashlib.sha1(b"example:release").hexdigest()
RUN = {"repository": "example-user/hb-runs", "run_id": 18000000001, "run_attempt": 1}
HARNESS_ID = "baseline"
TASK_ID = "fix-pagination-off-by-one"

BUG = "    start = number * size\n"
FIX = "    start = (number - 1) * size\n"
WRONG = "    start = (number - 1) * (size - 1)\n"

# Three reps: a clean fix; a fix that also edited the public test; a wrong fix
# that ran into the wall-clock limit. Every rep took one attempt: with only
# three reps in this one-task suite, a single infra re-run would exceed the
# suite's re-run cap and the run could not be sealed.
REPS = [
    {"rep": 1, "attempts": 1, "start": 1760710000000, "seconds": 412, "termination": "exited",
     "exit_code": 0, "calls": 6, "line": FIX, "edit_tests": False},
    {"rep": 2, "attempts": 1, "start": 1760710600000, "seconds": 603, "termination": "exited",
     "exit_code": 0, "calls": 9, "line": FIX, "edit_tests": True},
    {"rep": 3, "attempts": 1, "start": 1760711300000, "seconds": 1200, "termination": "wall_timeout",
     "exit_code": 137, "calls": 12, "line": WRONG, "edit_tests": False},
]


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def text_patch(changes: list[tuple[str, str, str]]) -> bytes:
    """Git-format text diff for (path, before, after) triples, sorted by path."""
    out = []
    for rel, before, after in sorted(changes):
        body = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        if body:
            out.append(f"diff --git a/{rel} b/{rel}\n{body}")
    return "".join(out).encode("utf-8")


def fake_digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def gateway_records(spec: dict) -> str:
    lines, tokens, clock = [], 0, spec["start"] + 2000
    last = spec["calls"] - 1
    for seq in range(spec["calls"]):
        input_tokens = 6000 + 2500 * seq
        cached = 0 if seq == 0 else input_tokens - 2600
        output_tokens = 150 + (37 * seq) % 400
        tokens += input_tokens + output_tokens
        stops = spec["termination"] == "exited" and seq == last
        calls = 0 if stops else 1
        malformed = spec["rep"] == 3 and seq == 4
        duration = 1500 + 210 * seq
        lines.append(json.dumps({
            "schema": "hb/gateway-record/1",
            "seq": seq,
            "started_at_ms": clock,
            "duration_ms": duration,
            "outcome": "ok",
            "http_status": 200,
            "upstream_attempts": 2 if (spec["rep"] == 2 and seq == 3) else 1,
            "stream": True,
            "model_rewritten": spec["rep"] == 2 and seq == 0,
            "usage": {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output_tokens},
            "finish_reason": "stop" if stops else "tool_calls",
            "tool_calls": {"count": calls, "arguments_valid_json": 0 if malformed else calls},
            "request_sha256": fake_digest(f"example:req:{TASK_ID}:{spec['rep']}:{seq}"),
            "response_sha256": fake_digest(f"example:resp:{TASK_ID}:{spec['rep']}:{seq}"),
            "totals": {"tokens": tokens, "calls": seq + 1},
        }, sort_keys=True))
        clock += duration + 4000
    return "\n".join(lines) + "\n"


def harness_log(spec: dict) -> str:
    lines = [f"[hb-baseline] task {TASK_ID} rep {spec['rep']}", "[hb-baseline] reading /task/instruction.md"]
    lines += [f"[hb-baseline] model call {i + 1}" for i in range(spec["calls"])]
    if spec["termination"] == "exited":
        lines.append("[hb-baseline] done")
    return "\n".join(lines) + "\n"


def generate(out: Path) -> None:
    suite_dir = EXAMPLES / "suite"
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    registry = json.loads((EXAMPLES / "registry.json").read_text(encoding="utf-8"))
    task_dir = suite_dir / "tasks" / TASK_ID
    repo = task_dir / "repo"

    cohort = cohort_from_suite(suite, tree_digest(suite_dir))
    entry = entry_from_registry(registry, HARNESS_ID)
    dump(out / "cohort.json", cohort)
    dump(out / "entry.json", entry)
    dump(out / "release.json", {
        "schema": "hb/release/1",
        "signer_workflow": SIGNER_WORKFLOW,
        "signer_sha": SIGNER_SHA,
        "toolkit_image": TOOLKIT_IMAGE,
        "suite_image": SUITE_IMAGE,
        "cohort_ids": [cohort_id(cohort)],
        "entry_ids": sorted(entry_id(entry_from_registry(registry, h["id"])) for h in registry["harnesses"]),
        "task_ids": sorted(t["id"] for t in suite["tasks"]),
    })

    pager = (repo / "pager.py").read_text(encoding="utf-8")
    tests = (repo / "tests" / "test_pager.py").read_text(encoding="utf-8")
    bundle = out / "run" / "task-bundles" / TASK_ID
    with tempfile.TemporaryDirectory() as scratch:
        for spec in REPS:
            rep_dir = bundle / "reps" / str(spec["rep"])
            changes = [("pager.py", pager, pager.replace(BUG, spec["line"]))]
            if spec["edit_tests"]:
                changes.append(("tests/test_pager.py", tests, tests.replace("== [1, 2]", "== [3, 4]")))
            patch = text_patch(changes)
            rep_dir.mkdir(parents=True)
            (rep_dir / "workspace.patch").write_bytes(patch)
            (rep_dir / "gateway.jsonl").write_text(gateway_records(spec), encoding="utf-8")
            (rep_dir / "harness.log").write_text(harness_log(spec), encoding="utf-8")
            dump(rep_dir / "lifecycle.json", {
                "schema": "hb/lifecycle/1",
                "task_id": TASK_ID,
                "rep": spec["rep"],
                "attempts": spec["attempts"],
                "harness_image": entry["harness"]["image"],
                "started_at_ms": spec["start"],
                "ended_at_ms": spec["start"] + spec["seconds"] * 1000,
                "termination": spec["termination"],
                "exit_code": spec["exit_code"],
            })
            dump(rep_dir / "grade.json", grade_rep(
                suite=suite, task_dir=task_dir, patch=patch, rep=spec["rep"], grader_image=TOOLKIT_IMAGE,
                workdir=Path(scratch) / f"rep{spec['rep']}", run_probe=run_probe_unsandboxed_for_tests))

        dump(bundle / MANIFEST_NAME, build_task_manifest(bundle, run=RUN, task_id=TASK_ID, reps=suite["reps"]))
        tar_path = Path(scratch) / f"task-bundle-{TASK_ID}.tar"
        digest = pack_bundle(bundle, tar_path)
        dump(out / "run" / "run-manifest.json", {
            "schema": "hb/run-manifest/1",
            "run": RUN,
            "release": {"toolkit_image": TOOLKIT_IMAGE, "suite_image": SUITE_IMAGE},
            "cohort": cohort,
            "entry": entry,
            "tasks": [{"task_id": TASK_ID, "bundle": {"sha256": digest, "size": tar_path.stat().st_size}}],
        })


def snapshot(root: Path) -> dict:
    files = {}
    for name in DERIVED:
        path = root / name
        items = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for item in items:
            files[item.relative_to(root).as_posix()] = item.read_bytes()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if committed examples are stale")
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            generate(Path(tmp))
            fresh, committed = snapshot(Path(tmp)), snapshot(EXAMPLES)
        stale = sorted(k for k in fresh.keys() | committed.keys() if fresh.get(k) != committed.get(k))
        if stale:
            print("stale example files (run scripts/make_examples.py):", *stale, sep="\n  ")
            return 1
        print("examples are up to date")
        return 0
    for name in DERIVED:
        target = EXAMPLES / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    generate(EXAMPLES)
    print("examples regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
