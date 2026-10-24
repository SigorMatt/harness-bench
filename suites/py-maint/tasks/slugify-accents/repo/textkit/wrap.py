"""Shortening text for display."""


def truncate(text, width, suffix="..."):
    """Return text cut to at most width characters, ending in suffix when it was cut.

    The cut falls on a space when there is one in the part that is kept, so
    words are not split.
    """
    if width < len(suffix) + 1:
        raise ValueError("width must leave room for at least one character and the suffix")
    if len(text) <= width:
        return text
    kept = text[:width - len(suffix)]
    if text[len(kept)] != " " and " " in kept:
        kept = kept[:kept.rindex(" ")]
    return kept.rstrip() + suffix


def initials(name):
    """Return the uppercase initials of a name: "ada lovelace" gives "AL"."""
    return "".join(part[0].upper() for part in name.split() if part)
