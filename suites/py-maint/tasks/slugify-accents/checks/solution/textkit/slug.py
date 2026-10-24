"""URL slugs."""

import re
import unicodedata

_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text, separator="-", max_length=None):
    """Return a URL slug for text.

    The slug holds lowercase ASCII letters and digits, with words joined by
    separator. Accented letters become their base letter ("é" becomes "e");
    any other character that is not a letter or a digit separates words.

    With max_length, whole words are dropped from the end until the slug is at
    most max_length characters long; a single word longer than that is cut.
    """
    if not separator or any(ch.isalnum() for ch in separator):
        raise ValueError("separator must be non-empty and hold no letters or digits")
    if max_length is not None and max_length < 1:
        raise ValueError("max_length must be at least 1")
    decomposed = unicodedata.normalize("NFKD", text)
    plain = decomposed.encode("ascii", "ignore").decode("ascii")
    words = [word for word in _NOT_ALNUM.split(plain.lower()) if word]
    slug = separator.join(words)
    if max_length is not None:
        while len(slug) > max_length and len(words) > 1:
            words.pop()
            slug = separator.join(words)
        slug = slug[:max_length]
    return slug
