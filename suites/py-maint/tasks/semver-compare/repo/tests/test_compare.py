from semverlite import compare, max_version


def test_numbers_are_compared_as_numbers():
    assert compare("1.10.0", "1.9.0") == 1


def test_a_prerelease_comes_before_its_release():
    assert compare("1.0.0-alpha", "1.0.0") == -1


def test_build_metadata_is_ignored():
    assert compare("1.0.0+a", "1.0.0+b") == 0


def test_max_version():
    assert str(max_version(["1.2.0", "1.10.0", "1.9.9"])) == "1.10.0"
