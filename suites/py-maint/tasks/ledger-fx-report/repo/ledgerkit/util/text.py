"""Text helpers for reports."""


def fit(text, width):
    """text cut to width characters, ending in "~" when it was cut."""
    return text if len(text) <= width else text[:max(width - 1, 0)] + "~"


def indent_account(name, depth_marker="  "):
    """An account name shown as its last part, indented by its depth: Assets:Bank:EUR becomes "    EUR"."""
    parts = name.split(":")
    return depth_marker * (len(parts) - 1) + parts[-1]
