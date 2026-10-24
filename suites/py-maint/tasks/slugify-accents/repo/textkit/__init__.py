"""Small text helpers for web pages: slugs and shortened text."""

from .slug import slugify
from .wrap import initials, truncate

__all__ = ["initials", "slugify", "truncate"]
__version__ = "1.4.0"
