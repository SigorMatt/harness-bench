"""Dates in journals: ISO dates, and periods between two of them."""

import datetime


def parse_date(text):
    """Read an ISO date (2024-01-31). Raises ValueError for anything else."""
    return datetime.date.fromisoformat(text.strip())


def in_period(day, start=None, end=None):
    """True if day is on or after start and on or before end; a missing bound is open."""
    return (start is None or day >= start) and (end is None or day <= end)


def month_bounds(year, month):
    """The first and last day of a month."""
    first = datetime.date(year, month, 1)
    following = datetime.date(year + month // 12, month % 12 + 1, 1)
    return first, following - datetime.timedelta(days=1)
