"""Render rows as Markdown tables."""

from .parse import parse_alignments
from .render import ALIGNMENTS, render

__all__ = ["ALIGNMENTS", "parse_alignments", "render"]
__version__ = "1.2.0"
