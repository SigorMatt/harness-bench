"""Reading .env files: KEY=VALUE lines, as shells and twelve-factor apps write them."""

import re

from .errors import EnvFileError

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "$": "$"}


def parse(text):
    """Return the variables a .env file's text sets, as a dict in file order.

    - Blank lines and lines starting with # are skipped.
    - A line may start with "export ".
    - A line is KEY=VALUE. The key is a shell variable name. Spaces around the
      key, and around a value without quotes, are dropped.
    - A value without quotes ends where " #" starts a comment.
    - Between 'single quotes', everything is kept as it is.
    - Between "double quotes", \\n, \\t, \\", \\\\ and \\$ stand for a newline,
      a tab, a double quote, a backslash and a dollar sign.
    - Only a comment may follow the closing quote.
    - When a key appears twice, the later value wins.

    Raises EnvFileError, which names the line, for a line it cannot read.
    """
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise EnvFileError(number, "expected KEY=VALUE")
        parts = line.split("=")
        key, value = parts[0].strip(), parts[1].strip()
        if not KEY.fullmatch(key):
            raise EnvFileError(number, f"{key!r} is not a variable name")
        values[key] = _value(value, number)
    return values


def _value(value, number):
    if value[:1] == "'":
        end = value.find("'", 1)
        if end < 0:
            raise EnvFileError(number, "unterminated single quote")
        _only_comment_after(value[end + 1:], number)
        return value[1:end]
    if value[:1] == '"':
        out, i = [], 1
        while i < len(value):
            ch = value[i]
            if ch == "\\" and i + 1 < len(value):
                out.append(_ESCAPES.get(value[i + 1], "\\" + value[i + 1]))
                i += 2
                continue
            if ch == '"':
                _only_comment_after(value[i + 1:], number)
                return "".join(out)
            out.append(ch)
            i += 1
        raise EnvFileError(number, "unterminated double quote")
    comment = value.find(" #")
    return (value[:comment] if comment >= 0 else value).rstrip()


def _only_comment_after(rest, number):
    rest = rest.strip()
    if rest and not rest.startswith("#"):
        raise EnvFileError(number, "unexpected text after the closing quote")


def load(path, encoding="utf-8"):
    """parse() the .env file at path."""
    with open(path, encoding=encoding) as fh:
        return parse(fh.read())
