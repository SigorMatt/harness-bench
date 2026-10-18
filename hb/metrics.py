"""Efficiency metrics, derived from trusted gateway records only."""

from __future__ import annotations

MICROS_PER_MTOK = 1_000_000


def rep_usage(records: list[dict]) -> dict:
    """Totals for one rep from its gateway.jsonl records."""
    return {
        "model_calls": len(records),
        "input_tokens": sum(r["usage"]["input_tokens"] for r in records),
        "cached_input_tokens": sum(r["usage"]["cached_input_tokens"] for r in records),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in records),
        "model_ms": sum(r["duration_ms"] for r in records),
        "tool_calls": sum(r["tool_calls"]["count"] for r in records),
        "tool_calls_invalid": sum(r["tool_calls"]["count"] - r["tool_calls"]["arguments_valid_json"] for r in records),
        "upstream_rejected": sum(1 for r in records if r["outcome"] == "upstream_rejected"),
    }


def cost_micros(usage: dict, price: dict) -> int:
    """Cost in micro-dollars, rounded up. Cached input is billed at its own rate."""
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    total = (uncached * price["input_micros_per_mtok"]
             + usage["cached_input_tokens"] * price["cached_input_micros_per_mtok"]
             + usage["output_tokens"] * price["output_micros_per_mtok"])
    return -(-total // MICROS_PER_MTOK)
