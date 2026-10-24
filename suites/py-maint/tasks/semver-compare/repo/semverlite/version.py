"""Semantic versions (https://semver.org, version 2.0.0): parsing and precedence."""

import functools
import re
from dataclasses import dataclass

_NUMBER = r"(?:0|[1-9][0-9]*)"
_IDENTIFIER = rf"(?:{_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(
    rf"({_NUMBER})\.({_NUMBER})\.({_NUMBER})"
    rf"(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@dataclass(frozen=True)
class Version:
    """A semantic version.

    prerelease and build hold the dot-separated identifiers after "-" and "+".
    == compares every field, build metadata included; compare() gives the
    precedence of two versions.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple = ()
    build: tuple = ()

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    @property
    def is_prerelease(self):
        return bool(self.prerelease)


def parse(text):
    """Return the Version that text spells; surrounding spaces are ignored.

    Raises TypeError if text is not a string and ValueError if it is not a
    semantic version.
    """
    if not isinstance(text, str):
        raise TypeError("a version is a string")
    match = _VERSION.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not a semantic version: {text!r}")
    major, minor, patch, prerelease, build = match.groups()
    return Version(int(major), int(minor), int(patch),
                   tuple(prerelease.split(".")) if prerelease else (),
                   tuple(build.split(".")) if build else ())


def _version(value):
    return value if isinstance(value, Version) else parse(value)


def compare(a, b):
    """Return -1, 0 or 1 as a has lower, the same or higher precedence than b.

    a and b are Version objects or strings, which parse() reads. Precedence is
    the one Semantic Versioning 2.0.0 defines; see the README.
    """
    raise NotImplementedError("compare() is not written yet")


def max_version(versions):
    """Return the version of highest precedence among versions, as a Version.

    Of versions with the same precedence, the first wins. Raises ValueError
    when there are no versions.
    """
    best = None
    for value in versions:
        version = _version(value)
        if best is None or compare(version, best) > 0:
            best = version
    if best is None:
        raise ValueError("max_version() of no versions")
    return best


def sort_versions(versions):
    """Return the versions as Version objects, from lowest to highest precedence.

    Versions with the same precedence keep their order.
    """
    return sorted((_version(value) for value in versions), key=functools.cmp_to_key(compare))


def bump(version, part):
    """Return the next version after version: part is "major", "minor" or "patch".

    The new version has no pre-release and no build metadata.
    """
    v = _version(version)
    if part == "major":
        return Version(v.major + 1, 0, 0)
    if part == "minor":
        return Version(v.major, v.minor + 1, 0)
    if part == "patch":
        return Version(v.major, v.minor, v.patch + 1)
    raise ValueError(f"part must be major, minor or patch, not {part!r}")
