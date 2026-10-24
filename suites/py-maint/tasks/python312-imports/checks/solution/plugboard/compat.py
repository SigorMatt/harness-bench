"""Version checks for plugins that need a newer host."""

def _parts(version):
    return tuple(int(part) for part in version.split("."))


def version_at_least(installed, required):
    """Return True if version installed is at least version required.

    Versions are dot-separated numbers, such as "2.10.1", compared part by part
    as numbers: "2.10" is later than "2.9".
    """
    return _parts(installed) >= _parts(required)
