import pytest

from mdtable import parse_alignments, render


def test_left_aligned_by_default():
    assert render(["a", "bb"], [["1", "2"]]) == (
        "| a   | bb  |\n"
        "| :-- | :-- |\n"
        "| 1   | 2   |\n"
    )


def test_centred_text_gets_the_odd_space_on_its_right():
    assert render(["c"], [["ab"]], align=["center"]) == (
        "|  c  |\n"
        "| :-: |\n"
        "| ab  |\n"
    )


def test_rows_must_have_a_cell_per_column():
    with pytest.raises(ValueError):
        render(["a", "b"], [["1"]])


def test_parse_alignments():
    assert parse_alignments("lrc") == ["left", "right", "center"]
