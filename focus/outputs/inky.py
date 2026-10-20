"""Draw today's open items and the coach's reply on a Pimoroni Inky Impression.

`render()` is pure Pillow, so the layout can be worked on without the panel:

    python -m focus.outputs.inky preview.png
                                draws what the panel would show now from
                                state.json into a PNG (no API call)
    python -m focus.outputs.inky --watch
                                the focus-display service: redraws the
                                panel whenever what it shows changes

The layout is drawn at the panel's own size (the 4.0" Spectra 6 is 600x400,
the older 4" model 640x400). A refresh takes 20-30 s and flashes, so the
watcher waits until changes settle (QUIET_S) and redraws only when the
picture would differ from what is already on the panel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .. import config, state as st
from ..display import shown, since_note
from ..layout import (HEADER_HEIGHT, HEADER_SIZE, MARGIN, NOTE_LINE, NOTE_SIZE, REPLY_LINE,
                      REPLY_MAX_SIZE, REPLY_MIN_SIZE, RULE, RULE_ABOVE, RULE_BELOW, SIZE,
                      TAG_SIZE, bullet_width, dot_size, item_size)
from ..state import State

log = logging.getLogger(__name__)

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)  # one accent; both panels have a clean red

POLL_S = 5
QUIET_S = 30  # no change for this long before a redraw, so a burst of changes is one refresh

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
    _warn_fallback()
    return ImageFont.load_default(size)


_warned = False


def _warn_fallback() -> None:
    global _warned
    if not _warned:
        log.warning("No DejaVu font (sudo apt install fonts-dejavu-core); using Pillow's, without bold")
        _warned = True


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
    text: str, width: float, height: float, max_size: int = REPLY_MAX_SIZE
) -> tuple[ImageFont.FreeTypeFont, list[str], int, bool]:
    """Largest font whose wrapped lines fit the box; truncate at the smallest.

    Returns (font, lines, line_height, truncated).
    """
    for size in range(max_size, REPLY_MIN_SIZE - 1, -1):
        font = _font(size)
        line_height = round(size * REPLY_LINE)
        lines = _wrap(text, font, width)
        if len(lines) * line_height <= height:
            return font, lines, line_height, False
    max_lines = max(1, int(height // line_height))
    lines = lines[:max_lines]
    lines[-1] = _ellipsize(lines[-1] + "…", font, width)
    return font, lines, line_height, True


def view(state: State) -> tuple[str | None, str, str]:
    """(slot or None, reply, since-note): what the panel shows for this state."""
    last = state.checkins[-1] if state.checkins else None
    if last is None:
        return None, "The first check-in of the day will appear here.", ""
    return last["slot"], last.get("reply") or "", since_note(state)


def render(state: State, slot: str | None, reply: str, since: str = "",
           size: tuple[int, int] = SIZE) -> Image.Image:
    img = Image.new("RGB", size, WHITE)
    draw = ImageDraw.Draw(img)
    right, bottom = size[0] - MARGIN, size[1] - MARGIN
    width = size[0] - 2 * MARGIN
    y = MARGIN

    header = _font(HEADER_SIZE, bold=True)
    draw.text((MARGIN, y), f"{slot.upper()} CHECK-IN" if slot else "NO CHECK-IN YET", font=header, fill=RED)
    day = date.fromisoformat(state.date).strftime("%a %d %b")
    draw.text((right, y), day, font=header, fill=BLACK, anchor="ra")
    y += HEADER_HEIGHT

    items, more = shown(state)
    size, line = item_size(max(1, len(items) + (1 if more else 0)))
    pfont = _font(size, bold=True)
    tag_font = _font(TAG_SIZE, bold=True)
    # A drawn dot, not a "•" glyph: not every font has one.
    dot = dot_size(size)
    bullet_w = bullet_width(size)
    rows = 0
    if not items:
        draw.text((MARGIN, y), "Nothing open on the list", font=pfont, fill=BLACK)
        rows = 1
    for p in items:
        row = y + rows * line
        tag = f"{p.carried_days}d" if p.carried_days else ""
        tag_w = tag_font.getlength(tag) + 12 if tag else 0
        baseline = row + size  # item and tag share it, whatever their sizes
        middle = baseline - size * 0.36   # about the middle of a lowercase letter
        draw.ellipse((MARGIN, middle - dot / 2, MARGIN + dot, middle + dot / 2), fill=RED)
        text = _ellipsize(p.text, pfont, width - bullet_w - tag_w)
        draw.text((MARGIN + bullet_w, baseline), text, font=pfont, fill=BLACK, anchor="ls")
        if tag:
            draw.text((right, baseline), tag, font=tag_font, fill=RED, anchor="rs")
        rows += 1
    if more:
        draw.text((MARGIN + bullet_w, y + rows * line), f"+{more} more", font=_font(size), fill=RED)
        rows += 1
    y += rows * line + RULE_ABOVE

    draw.line((MARGIN, y, right, y), fill=BLACK, width=RULE)
    y += RULE_BELOW

    reply_bottom = bottom - (NOTE_LINE if since else 0)
    font, lines, line_height, truncated = fit_reply(reply, width, reply_bottom - y,
                                                    max_size=min(REPLY_MAX_SIZE, size))
    if truncated:
        log.warning("Reply too long for the display; truncated to %d lines", len(lines))
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=BLACK)
        y += line_height

    if since:
        note = _font(NOTE_SIZE, bold=True)
        draw.text((MARGIN, bottom - NOTE_LINE + 6), _ellipsize(since, note, width), font=note, fill=RED)

    return img


def fingerprint(state: State, slot: str | None, reply: str, since: str = "") -> str:
    """Changes exactly when the drawn picture would."""
    items, more = shown(state)
    listed = [(p.text, p.carried_days) for p in items]
    return hashlib.sha256(json.dumps([state.date, slot, listed, more, reply, since]).encode()).hexdigest()


class Panel:
    """The attached Inky, found on first use (inky reads its model from the
    panel's ID chip). Or a PNG file, to try the watcher without the panel."""

    def __init__(self, png_path: Path | None = None) -> None:
        self.png_path = png_path
        self._inky = None

    @property
    def size(self) -> tuple[int, int]:
        if self.png_path:
            return SIZE
        return tuple(self._device().resolution)

    def _device(self):
        if self._inky is None:
            from inky.auto import auto  # only on the Pi with the panel attached
            self._inky = auto(ask_user=False, verbose=False)
            log.info("Found a %s panel, %dx%d", type(self._inky).__name__, *self._inky.resolution)
        return self._inky

    def show(self, img: Image.Image) -> None:
        if self.png_path:
            img.save(self.png_path)
            log.info("Saved display preview to %s", self.png_path)
            return
        device = self._device()
        device.set_image(img)
        device.show()  # blocks for the 20-30 s refresh


class InkyOutput:
    """The `inky` check-in output: draws right after each check-in. With the
    focus-display service running, leave it out of FOCUS_OUTPUTS: the
    service already redraws on every change, and only one may drive the panel."""

    def __init__(self, marker: Path, panel: Panel) -> None:
        self.marker = marker
        self.panel = panel

    def checkin(self, state: State, slot: str, reply: str) -> None:
        since = since_note(state)
        key = fingerprint(state, slot, reply, since)
        if self.marker.is_file() and self.marker.read_text() == key:
            log.info("Display unchanged; skipping refresh")
            return
        self.panel.show(render(state, slot, reply, since, self.panel.size))
        self.marker.write_text(key)


def _panel() -> Panel:
    png = os.environ.get("FOCUS_INKY_PNG")
    return Panel(Path(png) if png else None)


def create() -> InkyOutput:
    return InkyOutput(config.load().state_dir / "inky.last", _panel())


def watch(cfg: config.Config, panel: Panel, *, quiet_s: float = QUIET_S, poll_s: float = POLL_S,
          clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
          running: Callable[[], bool] = lambda: True) -> None:
    """Redraw the panel whenever what it shows changes, once changes have
    settled for `quiet_s`. It always draws once after starting: something
    else may have drawn on the panel meanwhile (a test, the `inky` output),
    so a remembered fingerprint can't be trusted."""
    marker = cfg.state_dir / "inky.last"
    drawn = None
    pending, since_change = None, 0.0
    while running():
        try:
            s = st.peek(cfg)
        except st.StateError as exc:
            log.warning("Can't read the state: %s", exc)
            sleep(poll_s)
            continue
        slot, reply, since = view(s)
        key = fingerprint(s, slot, reply, since)
        if key == drawn:
            pending = None
        elif key != pending:
            pending, since_change = key, clock()
        elif clock() - since_change >= quiet_s:
            log.info("Redrawing the panel")
            try:
                panel.show(render(s, slot, reply, since, panel.size))
            except Exception:  # a flaky cable or a busy panel: try again after the next change or quiet spell
                log.exception("Drawing on the panel failed")
                since_change = clock()
            else:
                drawn, pending = key, None
                marker.write_text(key)
        sleep(poll_s)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--watch"]:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        cfg = config.load()
        panel = _panel()
        log.info("Watching %s; redrawing %d s after changes settle", cfg.state_path, QUIET_S)
        watch(cfg, panel)
        return 0
    out = Path(argv[0] if argv else "inky-preview.png")
    s = st.peek(config.load())
    slot, reply, since = view(s)
    render(s, slot, reply, since).save(out)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
