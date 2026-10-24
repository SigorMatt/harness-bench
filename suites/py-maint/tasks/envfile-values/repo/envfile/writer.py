"""Writing .env files that envfile.parse() reads back."""

import re

from .parser import KEY

_SAFE = re.compile(r"[A-Za-z0-9_./:@%+,=-]*")


def dumps(values):
    """Return .env text for values, a dict of str to str, that parse() reads back as values.

    A value of only letters, digits and _ . / : @ % + , = - is written as it is;
    any other value is written between double quotes, with escapes.
    """
    lines = []
    for key, value in values.items():
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError(f"{key!r} is not a variable name")
        if not isinstance(value, str):
            raise TypeError(f"the value of {key} is not a string")
        if _SAFE.fullmatch(value):
            lines.append(f"{key}={value}")
        else:
            escaped = (value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                       .replace("\t", "\\t").replace("$", "\\$"))
            lines.append(f'{key}="{escaped}"')
    return "".join(line + "\n" for line in lines)


def dump(values, path, encoding="utf-8"):
    """Write dumps(values) to the file at path."""
    with open(path, "w", encoding=encoding) as fh:
        fh.write(dumps(values))
