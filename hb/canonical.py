"""Canonical JSON and content-derived identifiers.

IDs are derived, never declared: an ID is the SHA-256 of an object's
canonical encoding. The canonical form is defined only for a restricted value
space -- objects with string keys, arrays, printable-ASCII strings, booleans
and integers within the IEEE-754 safe range -- so the encoding is unique
without a general-purpose canonicaliser. Floats and null are rejected.
"""

from __future__ import annotations

import hashlib
import json

MAX_SAFE_INT = 2**53 - 1


def _check(value, path: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise ValueError(f"{path}: integer outside the safe range")
        return
    if isinstance(value, str):
        if any(not 0x20 <= ord(ch) <= 0x7E for ch in value):
            raise ValueError(f"{path}: strings must be printable ASCII")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: object keys must be strings")
            _check(key, f"{path}.<key>")
            _check(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    raise TypeError(f"{path}: {type(value).__name__} is not allowed in canonical JSON")


def canonical_bytes(value) -> bytes:
    """Encode `value` as sorted-key, whitespace-free JSON (ASCII)."""
    _check(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def content_id(value) -> str:
    """Return 'sha256:<hex>' of the canonical encoding of `value`."""
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()
