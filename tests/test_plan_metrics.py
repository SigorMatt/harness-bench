import json
from pathlib import Path

import pytest

from hb.metrics import cost_micros, rep_usage
from hb.plan import MAX_JOB_MINUTES, task_job_timeout_minutes

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SUITE = json.loads((EXAMPLES / "suite/suite.json").read_text())
REP1 = EXAMPLES / "run/task-bundles/fix-pagination-off-by-one/reps/1/gateway.jsonl"


def test_job_timeout_covers_every_rep_using_every_attempt():
    # 3 reps x 3 attempts x (1200 wall + 30 grace + 120 drain + 30 probe + 60 overhead) s
    # + 600 s setup = 13560 s = 226 min
    assert task_job_timeout_minutes(SUITE, max_probe_timeout_seconds=30) == 226


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


def test_locally_answered_requests_are_not_model_calls():
    records = [json.loads(line) for line in REP1.read_text().splitlines()]
    local = dict(records[-1], outcome="budget_exhausted", upstream_attempts=0, duration_ms=1,
                 usage={"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
                 tool_calls={"count": 0, "arguments_valid_json": 0})
    with_local = rep_usage(records + [local])
    assert with_local["model_calls"] == rep_usage(records)["model_calls"] == 6
    assert with_local["model_ms"] == rep_usage(records)["model_ms"]
    unknown = dict(local, outcome="usage_unknown", upstream_attempts=1, http_status=504)
    with_unknown = rep_usage(records + [unknown])
    assert with_unknown["model_calls"] == 7 and with_unknown["usage_unknown"] == 1


def test_cost_rounds_up_to_whole_micro_dollars():
    price = {"input_micros_per_mtok": 1, "cached_input_micros_per_mtok": 0, "output_micros_per_mtok": 0}
    assert cost_micros({"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0}, price) == 1
