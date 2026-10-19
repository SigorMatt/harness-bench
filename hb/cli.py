"""Command line of the toolkit image: `python3 -m hb <command>`.

    gateway   the model gateway (hb/gateway.py), started by run-task in its own container
    run-task  every rep of one task, then /out/task-bundle-<task>.tar (hb/orchestrator.py)

run-task takes everything that identifies the job from the environment the
pinned workflow sets, so nothing is interpolated into a shell:

    HARNESS_ID      registry id of the harness under test
    TASK_ID         suite task to run
    HB_REPOSITORY, HB_RUN_ID, HB_RUN_ATTEMPT   the GitHub Actions run
    HB_TOOLKIT_IMAGE  this image, by digest (gateway, helper and probe containers use it)
    MODEL_API_KEY   handed to the gateway container by name only

Exit status: 0 with the bundle written, 1 if the job failed, 2 for bad inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

DEFAULT_REGISTRY = "/opt/hb/registry.json"

# Same patterns as schemas/common.schema.json.
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
REPOSITORY = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
IMAGE_REF = re.compile(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}")
POSINT = re.compile(r"[1-9][0-9]{0,15}")


class InputError(Exception):
    """A missing or malformed input. Exit status 2."""


def _env(env: dict, name: str, pattern: re.Pattern) -> str:
    value = env.get(name, "")
    if not pattern.fullmatch(value) or (pattern is IMAGE_REF and len(value) > 255):
        raise InputError(f"{name} is missing or malformed")
    return value


def job_from_env(env: dict, suite_dir: Path, registry_path: Path):
    """Build the TaskJob for run-task from the environment, the suite and the registry."""
    from .identity import entry_from_registry
    from .orchestrator import TaskJob

    suite_dir = Path(suite_dir).resolve()
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    harness_id = _env(env, "HARNESS_ID", SLUG)
    task_id = _env(env, "TASK_ID", SLUG)
    if task_id not in [t["id"] for t in suite["tasks"]]:
        raise InputError(f"TASK_ID {task_id!r} is not a task of suite {suite['id']} {suite['version']}")
    try:
        entry_from_registry(registry, harness_id)
    except KeyError:
        raise InputError(f"HARNESS_ID {harness_id!r} is not in the registry") from None
    harness = next(h for h in registry["harnesses"] if h["id"] == harness_id)
    run = {
        "repository": _env(env, "HB_REPOSITORY", REPOSITORY),
        "run_id": int(_env(env, "HB_RUN_ID", POSINT)),
        "run_attempt": int(_env(env, "HB_RUN_ATTEMPT", POSINT)),
    }
    if not env.get("MODEL_API_KEY"):
        raise InputError("MODEL_API_KEY is not set")
    return TaskJob(suite=suite, suite_dir=suite_dir, task_id=task_id, harness=harness,
                   toolkit_image=_env(env, "HB_TOOLKIT_IMAGE", IMAGE_REF), run=run)


def _terminate(signum, frame):
    # Ignore further signals (a cancelled job sends SIGINT, then SIGTERM), so the
    # cleanup this starts runs once and finishes.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def run_task_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="hb run-task", description="run every rep of one task")
    parser.add_argument("--suite", required=True, type=Path, help="extracted suite directory")
    parser.add_argument("--out", required=True, type=Path, help="directory for task-bundle-<task>.tar")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, type=Path, help="harness registry")
    parser.add_argument("--work", type=Path, default=None, help="new scratch directory (default: a temporary one)")
    args = parser.parse_args(argv)

    from .dockercli import Clock, Docker, DockerError
    from .orchestrator import RunTaskError, run_task
    from .patch import PatchError
    from .transfer import TransferError

    try:
        job = job_from_env(os.environ, args.suite, args.registry)
    except (InputError, OSError, ValueError, KeyError) as exc:
        print(f"hb run-task: {exc}", file=sys.stderr)
        return 2
    work = args.work or Path(tempfile.mkdtemp(prefix="hb-run-task-")) / "work"
    # A cancelled job sends SIGINT and SIGTERM. Raise, so cleanup (finally blocks) removes the containers.
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    try:
        bundle = run_task(job, docker=Docker(), clock=Clock(), out_dir=args.out, work_dir=work)
    except (RunTaskError, DockerError, TransferError, PatchError) as exc:
        print(f"hb run-task: failed: {exc}", file=sys.stderr)
        return 1
    print(f"hb run-task: wrote {bundle}", file=sys.stderr)
    return 0


COMMANDS = ("gateway", "run-task")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python3 -m hb {{{','.join(COMMANDS)}}} [options]", file=sys.stderr)
        return 2
    if argv[0] == "gateway":
        from .gateway import main as gateway_main
        return gateway_main(argv[1:])
    return run_task_main(argv[1:])
