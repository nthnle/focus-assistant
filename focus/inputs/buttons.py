"""The Inky Impression's four buttons, read by the focus-display service.

    A   mark done the item the latest check-in suggested (as the Call button does)
    B   undo the last "done"
    C   pause for 30 minutes, or resume if paused
    D   redraw the panel now

FOCUS_INKY_BUTTONS lists their GPIO pins, A to D (default 5,6,16,24, the 4"
panel's; empty turns them off). A refresh takes 20-30 s, so after a press the
panel redraws straight away instead of waiting for changes to settle. The
service has no speaker and no network: the panel is the only answer, and the
check-in isn't rewritten (its note line says what was done since).
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Iterator

from .. import actions, config, pause, state as st
from ..config import Config

log = logging.getLogger(__name__)

LABELS = "ABCD"
DEFAULT_PINS = "5,6,16,24"
PAUSE = timedelta(minutes=30)
DEBOUNCE = timedelta(milliseconds=20)
REPEAT_S = 0.5   # the same button again this soon is a bounce, not a second press


def mark_suggested_done(cfg: Config) -> str:
    """Mark done the item the latest check-in suggested starting with; returns what to say."""
    with st.locked_state(cfg) as s:
        if not s.open_priorities:
            return "Nothing left to mark done."
        item = s.suggested
        if item is None:
            return "Tell me which one is done."
        actions.complete_priority(s, item.id)
        left = len(s.open_priorities)
    log.info("Marked %s done", config.words(item.text))
    return f"Done: {item.text}. " + (f"{left} left on your list." if left else "That's everything on your list.")


def undo_last_done(cfg: Config) -> str:
    """Reopen whatever was marked done most recently."""
    with st.locked_state(cfg) as s:
        done = [p for p in s.priorities if p.done]
        if not done:
            return "Nothing to undo."
        last = max(done, key=lambda p: p.done_at)
        actions.reopen_priority(s, last.id)
    log.info("Reopened %s", config.words(last.text))
    return f"Undone: {last.text} is back on your list."


def toggle_pause(cfg: Config, now: datetime) -> str:
    """Resume if paused, else pause for PAUSE. `now` must be timezone-aware."""
    with st.locked_state(cfg, today=now.date()) as s:
        if pause.paused(s, now):
            actions.resume(s)
            return "Resumed."
        actions.pause(s, now + PAUSE)
        return f"Paused {pause.describe(s, now)}."


def press(cfg: Config, label: str, now: datetime) -> str:
    """Do what button `label` does; returns what happened, for the log."""
    if label == "A":
        return mark_suggested_done(cfg)
    if label == "B":
        return undo_last_done(cfg)
    if label == "C":
        return toggle_pause(cfg, now)
    return "Redrawing."


def pins() -> list[int]:
    raw = os.environ.get("FOCUS_INKY_BUTTONS", DEFAULT_PINS)
    return [int(p) for p in raw.split(",") if p.strip()][:len(LABELS)]


def read_presses(gpio_pins: list[int]) -> Iterator[str]:
    """Button labels as they're pressed; blocks. gpiod comes with inky."""
    import gpiod
    import gpiodevice
    from gpiod.line import Bias, Direction, Edge

    chip = gpiodevice.find_chip_by_platform()
    offsets = [chip.line_offset_from_id(pin) for pin in gpio_pins]
    settings = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP,
                                  edge_detection=Edge.FALLING, debounce_period=DEBOUNCE)
    with chip.request_lines(consumer="focus-buttons", config=dict.fromkeys(offsets, settings)) as request:
        log.info("Listening to the panel's buttons on GPIO %s", ",".join(map(str, gpio_pins)))
        while True:
            for event in request.read_edge_events():
                yield LABELS[offsets.index(event.line_offset)]


class Presses:
    """Presses gathered by a reader thread, for the display watcher to wait on.
    If the buttons can't be read, it logs why and the panel works without them."""

    def __init__(self, source: Iterator[str], clock: Callable[[], float] = time.monotonic):
        self.queue: queue.Queue[str] = queue.Queue()
        self.clock = clock
        threading.Thread(target=self._pump, args=(source,), daemon=True).start()

    def _pump(self, source: Iterator[str]) -> None:
        last: dict[str, float] = {}
        try:
            for label in source:
                now = self.clock()
                if now - last.get(label, float("-inf")) >= REPEAT_S:
                    self.queue.put(label)
                last[label] = now
        except Exception:
            log.exception("The panel's buttons stopped; the panel still redraws on changes")

    def wait(self, timeout: float) -> list[str]:
        """The presses so far, waiting up to `timeout` seconds for the first."""
        try:
            pressed = [self.queue.get(timeout=timeout)]
        except queue.Empty:
            return []
        while True:
            try:
                pressed.append(self.queue.get_nowait())
            except queue.Empty:
                return pressed
