"""Values the workflow derives from the suite instead of hard-coding."""

from __future__ import annotations

MAX_JOB_MINUTES = 350             # GitHub-hosted jobs stop at 6 hours; keep a margin
BUDGET_GRACE_SECONDS = 30         # time a harness gets to exit after its budget is spent
PER_ATTEMPT_OVERHEAD_SECONDS = 60  # container start, snapshot, diff, advisory grading
JOB_SETUP_SECONDS = 600           # image pulls and suite extraction


def task_job_timeout_minutes(suite: dict, max_probe_timeout_seconds: int) -> int:
    """Worst-case duration of one task job: every rep using every allowed attempt.

    Raises ValueError if the suite cannot fit in a GitHub-hosted job, so such a
    suite fails at planning time rather than in the middle of a run.
    """
    per_attempt = (suite["budget"]["wall_seconds"] + BUDGET_GRACE_SECONDS
                   + max_probe_timeout_seconds + PER_ATTEMPT_OVERHEAD_SECONDS)
    attempts = suite["reps"] * (1 + suite["infra"]["max_reruns_per_rep"])
    minutes = -(-(attempts * per_attempt + JOB_SETUP_SECONDS) // 60)
    if minutes > MAX_JOB_MINUTES:
        raise ValueError(f"suite needs up to {minutes} minutes per task job; the limit is {MAX_JOB_MINUTES}")
    return minutes
