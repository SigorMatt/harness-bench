"""Efficiency metrics, derived from trusted gateway records only."""

from __future__ import annotations

MICROS_PER_MTOK = 1_000_000


def _forwarded(record: dict) -> bool:
    """True if the request reached the provider (the gateway did not answer it itself)."""
    return record["upstream_attempts"] > 0


def rep_usage(records: list[dict]) -> dict:
    """Totals for one rep from its gateway.jsonl records.

    model_calls counts requests that reached the provider; requests the gateway
    answered itself (bad_request, budget_exhausted) are not model calls.
    model_ms is the time spent on those requests, including retry backoff and
    relaying the answer to the harness.
    """
    return {
        "model_calls": sum(1 for r in records if _forwarded(r)),
        "input_tokens": sum(r["usage"]["input_tokens"] for r in records),
        "cached_input_tokens": sum(r["usage"]["cached_input_tokens"] for r in records),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in records),
        "model_ms": sum(r["duration_ms"] for r in records if _forwarded(r)),
        "tool_calls": sum(r["tool_calls"]["count"] for r in records),
        "tool_calls_invalid": sum(r["tool_calls"]["count"] - r["tool_calls"]["arguments_valid_json"] for r in records),
        "upstream_rejected": sum(1 for r in records if r["outcome"] == "upstream_rejected"),
        # Requests whose tokens are not in the totals above (the gateway stopped waiting).
        "usage_unknown": sum(1 for r in records if r["outcome"] == "usage_unknown"),
    }


def cost_micros(usage: dict, price: dict) -> int:
    """Cost in micro-dollars, rounded up. Cached input is billed at its own rate."""
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    total = (uncached * price["input_micros_per_mtok"]
             + usage["cached_input_tokens"] * price["cached_input_micros_per_mtok"]
             + usage["output_tokens"] * price["output_micros_per_mtok"])
    return -(-total // MICROS_PER_MTOK)
