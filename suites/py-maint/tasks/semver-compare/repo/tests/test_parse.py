import pytest

from semverlite import Version, bump, parse


def test_parse_a_full_version():
    assert parse("1.0.0-alpha.1+build.5") == Version(1, 0, 0, ("alpha", "1"), ("build", "5"))


def test_str_gives_the_version_back():
    assert str(parse("2.3.4-rc.1+exp.sha.5114f85")) == "2.3.4-rc.1+exp.sha.5114f85"


@pytest.mark.parametrize("text", ["1.2", "01.2.3", "1.0.0-01", "v1.0.0", "1.0.0-"])
def test_invalid_versions_are_refused(text):
    with pytest.raises(ValueError):
        parse(text)


def test_bump_drops_the_prerelease():
    assert str(bump("1.4.7-rc.1", "minor")) == "1.5.0"
