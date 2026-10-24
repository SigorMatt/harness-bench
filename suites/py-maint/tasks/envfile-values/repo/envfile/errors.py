"""Errors raised while reading or writing .env files."""


class EnvFileError(ValueError):
    """A line of a .env file that cannot be read; line is its 1-based number."""

    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line
