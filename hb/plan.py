"""Values the workflow derives from the suite instead of hard-coding.

plan_run() is what the plan job computes before any task job starts: the task
matrix and the task job timeout. It refuses a suite the run could not complete,
so such a suite fails before any tokens are spent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_JOB_MINUTES = 350             # GitHub-hosted jobs stop at 6 hours; keep a margin
BUDGET_GRACE_SECONDS = 30         # time a harness gets to exit after its budget is spent
GATEWAY_DRAIN_SECONDS = 120       # hb.gateway --drain-s: requests in flight when the harness has gone
PER_ATTEMPT_OVERHEAD_SECONDS = 60  # container start, snapshot, diff, advisory grading
JOB_SETUP_SECONDS = 600           # image pulls and suite extraction


def task_job_timeout_minutes(suite: dict, max_probe_timeout_seconds: int) -> int:
    """Worst-case duration of one task job: every rep using every allowed attempt.

    An estimate, not a bound: the toolkit's own limits (the snapshot timeout, the
    gateway's stop grace) allow an attempt more than PER_ATTEMPT_OVERHEAD_SECONDS,
    and the diff is not timed (spec §10). Raises ValueError if the suite cannot fit in a GitHub-hosted job, so such a
    suite fails at planning time rather than in the middle of a run.
    """
    per_attempt = (suite["budget"]["wall_seconds"] + BUDGET_GRACE_SECONDS + GATEWAY_DRAIN_SECONDS
                   + max_probe_timeout_seconds + PER_ATTEMPT_OVERHEAD_SECONDS)
    attempts = suite["reps"] * (1 + suite["infra"]["max_reruns_per_rep"])
    minutes = -(-(attempts * per_attempt + JOB_SETUP_SECONDS) // 60)
    if minutes > MAX_JOB_MINUTES:
        raise ValueError(f"suite needs up to {minutes} minutes per task job; the limit is {MAX_JOB_MINUTES}")
    return minutes


MAX_TASKS = 256  # one task job per task, in one GitHub Actions matrix
TASK_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")  # the slug of schemas/common.schema.json


class PlanError(ValueError):
    """The suite cannot be run as it is."""


def task_ids(suite: dict) -> list[str]:
    """The suite's task ids: slugs, sorted and unique, between 1 and MAX_TASKS of them."""
    ids = [task["id"] for task in suite["tasks"]]
    if not 1 <= len(ids) <= MAX_TASKS:
        raise PlanError(f"a suite has between 1 and {MAX_TASKS} tasks, this one has {len(ids)}")
    if any(not isinstance(i, str) or not TASK_ID.fullmatch(i) for i in ids):
        raise PlanError("task ids must be slugs")
    if ids != sorted(set(ids)):
        raise PlanError("task ids must be sorted and unique")
    return ids


def plan_run(suite_dir: Path) -> dict:
    """{"tasks": [...], "timeout_minutes": n} for a run of the suite in suite_dir.

    Every listed task must have tasks/<id>/task.json with the same id. Raises
    PlanError, or ValueError when the suite cannot fit in a task job.
    """
    suite_dir = Path(suite_dir)
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    ids = task_ids(suite)
    probe_timeouts = []
    for task_id in ids:
        path = suite_dir / "tasks" / task_id / "task.json"
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanError(f"task {task_id}: cannot read task.json ({exc})") from None
        if not isinstance(task, dict) or task.get("id") != task_id:
            raise PlanError(f"task {task_id}: task.json does not name this task")
        probe = task.get("probe") if isinstance(task, dict) else None
        timeout = probe.get("timeout_seconds") if isinstance(probe, dict) else None
        if type(timeout) is not int or timeout < 1:
            raise PlanError(f"task {task_id}: the probe has no valid timeout")
        probe_timeouts.append(timeout)
    return {"tasks": ids, "timeout_minutes": task_job_timeout_minutes(suite, max(probe_timeouts))}
