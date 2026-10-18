import json
from pathlib import Path

import pytest

from hb.metrics import cost_micros, rep_usage
from hb.plan import MAX_JOB_MINUTES, task_job_timeout_minutes

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SUITE = json.loads((EXAMPLES / "suite/suite.json").read_text())
REP1 = EXAMPLES / "run/task-bundles/fix-pagination-off-by-one/reps/1/gateway.jsonl"


def test_job_timeout_covers_every_rep_using_every_attempt():
    # 3 reps x 3 attempts x (1200 + 30 + 30 + 60) s + 600 s setup = 12480 s = 208 min
    assert task_job_timeout_minutes(SUITE, max_probe_timeout_seconds=30) == 208


def test_a_suite_that_cannot_fit_in_a_job_is_refused_at_planning():
    suite = json.loads(json.dumps(SUITE))
    suite["budget"]["wall_seconds"] = 3600
    with pytest.raises(ValueError):
        task_job_timeout_minutes(suite, max_probe_timeout_seconds=30)
    assert MAX_JOB_MINUTES < 360


def test_usage_and_cost_from_gateway_records():
    records = [json.loads(line) for line in REP1.read_text().splitlines()]
    usage = rep_usage(records)
    assert usage["model_calls"] == len(records) == 6
    assert usage["input_tokens"] == sum(6000 + 2500 * i for i in range(6))
    prices = json.loads((EXAMPLES / "prices.json").read_text())["entries"][0]
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    expected = -(-(uncached * 100000 + usage["cached_input_tokens"] * 10000 + usage["output_tokens"] * 300000) // 10**6)
    assert cost_micros(usage, prices) == expected


def test_cost_rounds_up_to_whole_micro_dollars():
    price = {"input_micros_per_mtok": 1, "cached_input_micros_per_mtok": 0, "output_micros_per_mtok": 0}
    assert cost_micros({"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0}, price) == 1
