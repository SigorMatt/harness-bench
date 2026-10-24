"""Read and write .env files."""

from .errors import EnvFileError
from .parser import load, parse
from .writer import dump, dumps

__all__ = ["EnvFileError", "dump", "dumps", "load", "parse"]
__version__ = "0.9.2"
