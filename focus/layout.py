"""The e-ink panel's layout in numbers, shared by the panel (`outputs/inky.py`,
in pixels) and the status page (`display.py`, in panel pixels scaled to the
browser), so the two look the same.
"""

from __future__ import annotations

SIZE = (600, 400)  # the 4.0" Spectra 6; the panel's own size is used when it is attached
MARGIN = 24
HEADER_SIZE = 18
HEADER_HEIGHT = 40   # the list starts this far below the top margin
TAG_SIZE = 15        # "3d", days carried
# (rows up to, font size, line height): a short list gets bigger text; six
# rows of the smallest still leave the reply about six lines.
ITEM_SIZES = ((3, 26, 36), (4, 23, 32), (6, 19, 26))
RULE_ABOVE, RULE, RULE_BELOW = 8, 2, 16
REPLY_MAX_SIZE = 24  # and never larger than the list's text
REPLY_MIN_SIZE = 16
REPLY_LINE = 1.3     # line height, times the font size
NOTE_SIZE = 17       # the red "Since 09:00: done ..." line
NOTE_LINE = 26


def item_size(rows: int) -> tuple[int, int]:
    """(font size, line height) for a list of this many rows."""
    for most, size, line in ITEM_SIZES:
        if rows <= most:
            return size, line
    return ITEM_SIZES[-1][1:]


def dot_size(size: int) -> int:
    """The red bullet's diameter for items of this font size."""
    return max(4, size // 4)


def bullet_width(size: int) -> float:
    """From the left margin to where an item's text starts."""
    return dot_size(size) + size * 0.6
