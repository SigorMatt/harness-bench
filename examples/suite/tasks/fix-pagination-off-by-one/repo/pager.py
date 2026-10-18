"""Tiny pagination helpers."""


def page(items, number, size):
    """Return page `number` (1-based) of `items`, with `size` items per page."""
    if size < 1:
        raise ValueError("size must be >= 1")
    if number < 1:
        raise ValueError("number must be >= 1")
    start = number * size
    return items[start:start + size]


def page_count(total, size):
    """Return how many pages are needed for `total` items."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return -(-total // size)
