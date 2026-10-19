"""Draw today's open items and the coach's reply on the Inky Impression 4".

`render()` is pure Pillow, so the layout can be worked on without the panel:

    FOCUS_INKY_PNG=preview.png  makes the output save a PNG instead of
                                touching hardware.
    python -m focus.outputs.inky preview.png
                                redraws the last check-in in state.json
                                (no API call).

A full refresh takes about 30 s, so the panel is only redrawn when the items
shown or the reply have changed since the last draw.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import config, state as st
from ..display import shown
from ..state import State

log = logging.getLogger(__name__)

SIZE = (640, 400)
MARGIN = 24
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)  # one accent; the 7-colour panel has a clean red

HEADER_SIZE = 18
ITEM_SIZE = 19   # six rows of these leave the reply about six lines
ITEM_LINE = 26
REPLY_MAX_SIZE = 24
REPLY_MIN_SIZE = 16

# First match wins. DejaVu is on Pi OS (fonts-dejavu-core); Segoe covers
# Windows previews. Without either, Pillow's bundled font is used.
_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]
_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    override = os.environ.get("FOCUS_INKY_FONT")
    candidates = ([override] if override else []) + (_BOLD if bold else _REGULAR)
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _ellipsize(text: str, font: ImageFont.FreeTypeFont, width: float) -> str:
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        line = ""
        for word in para.split():
            trial = f"{line} {word}" if line else word
            if font.getlength(trial) <= width:
                line = trial
            elif line:
                lines.append(line)
                line = _ellipsize(word, font, width)
            else:
                line = _ellipsize(word, font, width)
        lines.append(line)
    return lines


def fit_reply(
    text: str, width: float, height: float
) -> tuple[ImageFont.FreeTypeFont, list[str], int, bool]:
    """Largest font whose wrapped lines fit the box; truncate at the smallest.

    Returns (font, lines, line_height, truncated).
    """
    for size in range(REPLY_MAX_SIZE, REPLY_MIN_SIZE - 1, -1):
        font = _font(size)
        line_height = round(size * 1.3)
        lines = _wrap(text, font, width)
        if len(lines) * line_height <= height:
            return font, lines, line_height, False
    max_lines = max(1, int(height // line_height))
    lines = lines[:max_lines]
    lines[-1] = _ellipsize(lines[-1] + "…", font, width)
    return font, lines, line_height, True


def render(state: State, slot: str, reply: str) -> Image.Image:
    img = Image.new("RGB", SIZE, WHITE)
    draw = ImageDraw.Draw(img)
    width = SIZE[0] - 2 * MARGIN
    y = MARGIN

    header = _font(HEADER_SIZE, bold=True)
    draw.text((MARGIN, y), f"{slot.upper()} CHECK-IN", font=header, fill=RED)
    day = date.fromisoformat(state.date).strftime("%a %d %b")
    draw.text((SIZE[0] - MARGIN, y), day, font=header, fill=BLACK, anchor="ra")
    y += 40

    pfont = _font(ITEM_SIZE, bold=True)
    tag_font = _font(HEADER_SIZE - 3, bold=True)
    items, more = shown(state)
    bullet = "\u2022  "
    bullet_w = pfont.getlength(bullet)
    rows = 0
    if not items:
        draw.text((MARGIN, y), "Nothing open on the list", font=pfont, fill=BLACK)
        rows = 1
    for p in items:
        row = y + rows * ITEM_LINE
        tag = f"{p.carried_days}d" if p.carried_days else ""
        tag_w = tag_font.getlength(tag) + 12 if tag else 0
        draw.text((MARGIN, row), bullet, font=pfont, fill=RED)
        text = _ellipsize(p.text, pfont, width - bullet_w - tag_w)
        draw.text((MARGIN + bullet_w, row), text, font=pfont, fill=BLACK)
        if tag:
            draw.text((SIZE[0] - MARGIN, row + 4), tag, font=tag_font, fill=RED, anchor="ra")
        rows += 1
    if more:
        draw.text((MARGIN + bullet_w, y + rows * ITEM_LINE), f"+{more} more", font=_font(ITEM_SIZE), fill=RED)
        rows += 1
    y += rows * ITEM_LINE + 8

    draw.line((MARGIN, y, SIZE[0] - MARGIN, y), fill=BLACK, width=2)
    y += 16

    font, lines, line_height, truncated = fit_reply(reply, width, SIZE[1] - MARGIN - y)
    if truncated:
        log.warning("Reply too long for the display; truncated to %d lines", len(lines))
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=BLACK)
        y += line_height

    return img


def fingerprint(state: State, reply: str) -> str:
    items, more = shown(state)
    listed = [(p.text, p.carried_days) for p in items]
    return hashlib.sha256(json.dumps([listed, more, reply]).encode()).hexdigest()


class InkyOutput:
    def __init__(self, marker: Path, png_path: Path | None = None) -> None:
        self.marker = marker
        self.png_path = png_path

    def checkin(self, state: State, slot: str, reply: str) -> None:
        key = fingerprint(state, reply)
        if self.marker.is_file() and self.marker.read_text() == key:
            log.info("Display unchanged; skipping refresh")
            return
        self._show(render(state, slot, reply))
        self.marker.write_text(key)

    def _show(self, img: Image.Image) -> None:
        if self.png_path:
            img.save(self.png_path)
            log.info("Saved display preview to %s", self.png_path)
            return
        from inky.auto import auto  # only on the Pi with the panel attached

        display = auto(ask_user=False, verbose=False)
        if tuple(display.resolution) != img.size:
            log.warning("Panel is %s, layout is %s; resizing", display.resolution, img.size)
            img = img.resize(display.resolution)
        display.set_image(img)
        display.show()


def create() -> InkyOutput:
    cfg = config.load()
    png = os.environ.get("FOCUS_INKY_PNG")
    return InkyOutput(cfg.state_dir / "inky.last", Path(png) if png else None)


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else "inky-preview.png")
    with st.locked_state(config.load()) as s:
        last = s.checkins[-1] if s.checkins else {"slot": "morning", "reply": "No check-in yet today."}
        render(s, last["slot"], last.get("reply") or "").save(out)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv[1:]))
