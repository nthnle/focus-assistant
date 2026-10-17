"""Is anyone at the desk? A best guess from the phone dock and desk sound.

You count as away only when both say so:

- the phone has been off the dock for FOCUS_AWAY_PHONE_MIN minutes (10),
  going by the dock's PHONE_OUT / PHONE_IN events, and
- the desk has been quiet for FOCUS_AWAY_QUIET_MIN minutes (15), going by the
  last time the voice service's microphone heard typing, a chair or a voice.
  Only that time is kept (presence.json), never audio. Without the voice
  service there is no sound reading, and the dock decides alone.

Leaving the phone on the dock when you walk off still counts as present; a
presence sensor would fix that. Away means stretch reminders are skipped and
a spoken check-in is held and said when you come back.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .state import PHONE_IN, PHONE_OUT, State

SOUND_WRITE_EVERY_S = 30
SOUND_FLOOR = 300.0


def _minutes(env: str, default: int) -> timedelta:
    return timedelta(minutes=int(os.environ.get(env, default)))


def sound_path(cfg: Config) -> Path:
    return cfg.state_dir / "presence.json"


def phone_out_since(state: State) -> datetime | None:
    """When the phone left the dock, if its last dock event says it is out."""
    for event in reversed(state.events):
        if event["kind"] == PHONE_IN:
            return None
        if event["kind"] == PHONE_OUT:
            return datetime.fromisoformat(event["at"])
    return None


def last_sound(cfg: Config) -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads(sound_path(cfg).read_text())["sound_at"])
    except (OSError, ValueError, KeyError):
        return None


def away(cfg: Config, state: State, now: datetime) -> bool:
    """`now` must be timezone-aware, like the timestamps in state."""
    out = phone_out_since(state)
    if out is None or now - out < _minutes("FOCUS_AWAY_PHONE_MIN", 10):
        return False
    heard = last_sound(cfg)
    return heard is None or now - heard >= _minutes("FOCUS_AWAY_QUIET_MIN", 15)


class SoundTracker:
    """Fed every microphone chunk's loudness; records when the desk last made a sound.

    The room's noise level is followed slowly from below, so a fan or hum
    doesn't count; anything clearly above it does. The time is written at
    most every 30 s, atomically, so readers never see a half-written file.
    """

    def __init__(self, cfg: Config, clock=lambda: datetime.now().astimezone()):
        self.path = sound_path(cfg)
        self.clock = clock
        self.noise: float | None = None
        self._written: datetime | None = None

    def feed(self, lvl: float) -> None:
        if self.noise is None or lvl < self.noise:
            self.noise = lvl
        else:
            self.noise += (lvl - self.noise) * 0.001  # creeps up over a minute or so
        if lvl < max(SOUND_FLOOR, 3 * self.noise):
            return
        now = self.clock()
        if self._written and (now - self._written).total_seconds() < SOUND_WRITE_EVERY_S:
            return
        self._written = now
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".presence-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"sound_at": now.isoformat(timespec="seconds")}, f)
        os.replace(tmp, self.path)
