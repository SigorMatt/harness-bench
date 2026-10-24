import pytest

from textkit import slugify


def test_words_are_joined_by_hyphens():
    assert slugify("Hello, World!") == "hello-world"


def test_custom_separator():
    assert slugify("Release notes 2024", separator="_") == "release_notes_2024"


def test_max_length_drops_whole_words():
    assert slugify("one two three", max_length=8) == "one-two"


def test_separator_must_not_hold_letters():
    with pytest.raises(ValueError):
        slugify("a b", separator="x")


def test_accents_are_transliterated():
    assert slugify("Crème Brûlée") == "creme-brulee"
