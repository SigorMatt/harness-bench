from textkit import initials, truncate


def test_short_text_is_kept():
    assert truncate("short", 10) == "short"


def test_long_text_is_cut_on_a_space():
    assert truncate("the quick brown fox", 12) == "the quick..."


def test_initials():
    assert initials("ada lovelace") == "AL"
