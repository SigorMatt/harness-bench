"""Rendering rows as a Markdown table."""

ALIGNMENTS = ("left", "right", "center")


def render(headers, rows, align=None):
    """Return a Markdown table with the given header cells and rows.

    Cells may be anything; they are shown with str(), None as an empty cell. A
    "|" in a cell is escaped and a line break becomes a space.

    align holds "left", "right" or "center" for each column (all "left" by
    default). Each cell is padded to its column's width: left-aligned text gets
    spaces on its right, right-aligned text on its left, and centred text on
    both sides, with the odd space on the right. The separator row marks each
    alignment the Markdown way: ":---", "---:" or ":--:". Columns are at least
    three characters wide.
    """
    columns = len(headers)
    align = list(align) if align is not None else ["left"] * columns
    if len(align) != columns:
        raise ValueError(f"align has {len(align)} entries for {columns} columns")
    for how in align:
        if how not in ALIGNMENTS:
            raise ValueError(f"unknown alignment {how!r}")
    table = [[_cell(value) for value in headers]] + [[_cell(value) for value in row] for row in rows]
    for number, row in enumerate(table[1:], 1):
        if len(row) != columns:
            raise ValueError(f"row {number} has {len(row)} cells, not {columns}")
    widths = [max([3] + [len(row[i]) for row in table]) for i in range(columns)]
    lines = [_line(table[0], widths, align), _separator(widths, align)]
    lines += [_line(row, widths, align) for row in table[1:]]
    return "\n".join(lines) + "\n"


def _cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def _pad(text, width, how):
    if how == "right":
        return text.ljust(width)
    if how == "center":
        left = (width - len(text)) // 2
        return " " * left + text + " " * (width - len(text) - left)
    return text.ljust(width)


def _line(cells, widths, align):
    return "| " + " | ".join(_pad(cell, width, how) for cell, width, how in zip(cells, widths, align)) + " |"


def _separator(widths, align):
    rules = []
    for width, how in zip(widths, align):
        if how == "left":
            rules.append(":" + "-" * (width - 1))
        elif how == "right":
            rules.append("-" * (width - 1) + ":")
        else:
            rules.append(":" + "-" * (width - 2) + ":")
    return "| " + " | ".join(rules) + " |"
