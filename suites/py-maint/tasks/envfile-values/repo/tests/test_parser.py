import pytest

from envfile import EnvFileError, parse


def test_comments_and_blank_lines_are_skipped():
    assert parse("# settings\n\nDEBUG=1\n") == {"DEBUG": "1"}


def test_export_prefix():
    assert parse("export HOST=localhost\n") == {"HOST": "localhost"}


def test_single_quotes_keep_everything():
    assert parse("PATTERN='a\\nb $x'\n") == {"PATTERN": "a\\nb $x"}


def test_double_quotes_understand_escapes():
    assert parse('MSG="hi\\nthere"\n') == {"MSG": "hi\nthere"}


def test_inline_comment_after_a_value():
    assert parse("PORT=8080 # default\n") == {"PORT": "8080"}


def test_a_line_without_equals_names_its_line():
    with pytest.raises(EnvFileError) as info:
        parse("A=1\nnonsense\n")
    assert info.value.line == 2
