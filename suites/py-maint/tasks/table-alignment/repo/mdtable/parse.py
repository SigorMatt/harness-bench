"""Column alignments from a short spec: one letter per column."""

_LETTERS = {"l": "left", "r": "right", "c": "center"}


def parse_alignments(spec):
    """Turn "lrc" into ["left", "right", "center"]; case and surrounding spaces do not matter.

    Raises ValueError for any other character.
    """
    # TODO: also accept "l,r,c", and words such as "left,right".
    alignments = []
    for letter in spec.strip().lower():
        if letter not in _LETTERS:
            raise ValueError(f"unknown alignment {letter!r}: use l, r or c")
        alignments.append(_LETTERS[letter])
    return alignments
