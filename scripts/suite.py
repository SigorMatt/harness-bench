#!/usr/bin/env python3
"""Suite steps (spec §7). Exits 0 when the step succeeds, 1 when it does not.

  check [SUITE_DIR]         (default: suites/py-maint) check every task against its schema and
                            the curation rules (hb/curate.py); needs jsonschema
        --toolkit IMAGE     run the probes in the probe sandbox, in IMAGE (a toolkit image such
                            as the release builds), as grading does; needs Docker. Without it
                            they run on this machine's python3, which may not be the sandbox's
                            Python 3.12 and may see packages the sandbox does not have.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.curate import curate_suite  # noqa: E402
from hb.dockercli import DockerError  # noqa: E402
from hb.grade import run_probe_unsandboxed_for_tests  # noqa: E402
from hb.plan import PlanError, plan_run  # noqa: E402
from hb.release import SUITE_DIR, ReleaseError, check_task_paths  # noqa: E402

SANDBOX_PYTHON = (3, 12)  # the toolkit image's, which probes run on (docker/toolkit-base.Dockerfile)


def cmd_check(args) -> int:
    from jsonschema import ValidationError

    from hb.schemas import validate

    suite_dir = Path(args.suite_dir).resolve()
    try:
        suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
        validate("suite", suite)
        plan = plan_run(suite_dir)
        for task_id in plan["tasks"]:
            task = json.loads((suite_dir / "tasks" / task_id / "task.json").read_text(encoding="utf-8"))
            validate("task", task)
            check_task_paths(task_id, suite_dir / "tasks" / task_id, task)
    except (OSError, ValueError, ValidationError, PlanError, ReleaseError) as exc:
        print(f"suite: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as scratch:
        if args.toolkit:
            from hb import profile
            from hb.dockercli import Docker
            from hb.orchestrator import DockerProbeRunner
            # Container and volume names of this check's own, so two checks at once do not share them.
            names = profile.probe_names(f"curation-{os.getpid()}", 1)
            run_probe = DockerProbeRunner(Docker(), args.toolkit, names, Path(scratch) / "sandbox")
        else:
            if sys.version_info[:2] != SANDBOX_PYTHON:
                print(f"suite: probes run on this machine's Python {sys.version_info[0]}.{sys.version_info[1]}, "
                      f"not the sandbox's {SANDBOX_PYTHON[0]}.{SANDBOX_PYTHON[1]}; --toolkit runs them there",
                      file=sys.stderr)
            run_probe = run_probe_unsandboxed_for_tests
        try:
            problems = curate_suite(suite_dir, run_probe, Path(scratch) / "curation")
        except DockerError as exc:  # the probe sandbox could not be set up; nothing is known about the tasks
            print(f"suite: {exc}", file=sys.stderr)
            return 1
    for task_id, found in problems.items():
        print(f"{task_id}: " + ("ok" if not found else "\n  ".join(["not fit to grade with:", *found])))
    return 1 if any(problems.values()) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("suite_dir", nargs="?", default=str(ROOT / SUITE_DIR))
    check.add_argument("--toolkit", help="run the probes in the probe sandbox, in this toolkit image")
    check.set_defaults(run=cmd_check)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
