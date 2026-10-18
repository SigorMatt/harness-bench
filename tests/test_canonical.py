import hashlib

import pytest

from hb.canonical import MAX_SAFE_INT, canonical_bytes, content_id


def test_encoding_is_sorted_and_compact():
    assert canonical_bytes({"b": [True, "x"], "a": 1}) == b'{"a":1,"b":[true,"x"]}'


def test_id_is_sha256_of_canonical_bytes():
    value = {"b": [True, "x"], "a": 1}
    assert content_id(value) == "sha256:" + hashlib.sha256(b'{"a":1,"b":[true,"x"]}').hexdigest()


def test_key_order_does_not_change_the_id():
    assert content_id({"a": 1, "b": {"c": 2, "d": 3}}) == content_id({"b": {"d": 3, "c": 2}, "a": 1})


@pytest.mark.parametrize("value", [1.5, None, "café", "line\nbreak", MAX_SAFE_INT + 1, (1, 2), {1: "x"}])
def test_values_outside_the_canonical_space_are_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        canonical_bytes({"v": value})
