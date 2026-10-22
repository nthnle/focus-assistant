"""Is anyone at the desk? A best guess from the desk camera and desk sound.

While the desk camera runs (focus blocks, not paused) and its model knows
the empty desk, it decides: you're away once it has seen the desk empty
for FOCUS_AWAY_CAMERA_MIN minutes (5). It writes what it sees to
camera.json; never a picture.

Otherwise (before and between blocks, while paused, or without a camera)
the microphone decides: you're away once the desk has been quiet for
FOCUS_AWAY_QUIET_MIN minutes (30), going by the last time the voice
service's microphone heard typing, a chair or a voice. Only that time is
kept (presence.json), never audio. Sitting silently that long counts as
away too, but a held check-in is said as soon as you're noticed again.
Without either, you always count as here.

Away means stretch reminders are skipped and a spoken check-in is held and
said when you come back.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .state import State

SOUND_WRITE_EVERY_S = 30
SOUND_FLOOR = 300.0
# The camera rewrites camera.json at least every 30 s while it runs; older means it stopped.
CAMERA_FRESH = timedelta(seconds=90)


def _minutes(env: str, default: int) -> timedelta:
    return timedelta(minutes=int(os.environ.get(env, default)))


def _write_json(path: Path, data: dict) -> None:
    """Atomically, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def sound_path(cfg: Config) -> Path:
    return cfg.state_dir / "presence.json"


def camera_path(cfg: Config) -> Path:
    return cfg.state_dir / "camera.json"


@dataclass(frozen=True)
class Seen:
    """What the desk camera sees: "phone", "work" or "away"."""
    label: str
    since: datetime
    at: datetime          # when it last wrote
    sees_away: bool       # its model knows the empty desk


def record_camera(cfg: Config, label: str, since: datetime, at: datetime, sees_away: bool) -> None:
    _write_json(camera_path(cfg), {
        "seen": label,
        "since": since.isoformat(timespec="seconds"),
        "at": at.isoformat(timespec="seconds"),
        "sees_away": sees_away,
    })


def clear_camera(cfg: Config) -> None:
    camera_path(cfg).unlink(missing_ok=True)


def camera_seen(cfg: Config, now: datetime) -> Seen | None:
    """What the camera sees now, or None when it isn't running."""
    try:
        data = json.loads(camera_path(cfg).read_text())
        seen = Seen(str(data["seen"]), datetime.fromisoformat(data["since"]),
                    datetime.fromisoformat(data["at"]), bool(data.get("sees_away")))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return seen if now - seen.at <= CAMERA_FRESH else None


def last_sound(cfg: Config) -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads(sound_path(cfg).read_text())["sound_at"])
    except (OSError, ValueError, KeyError):
        return None


def away(cfg: Config, state: State, now: datetime) -> bool:
    """`now` must be timezone-aware, like the timestamps in state."""
    seen = camera_seen(cfg, now)
    if seen is not None and seen.sees_away:
        return seen.label == "away" and now - seen.since >= _minutes("FOCUS_AWAY_CAMERA_MIN", 5)
    heard = last_sound(cfg)
    quiet = _minutes("FOCUS_AWAY_QUIET_MIN", 30)
    return heard is not None and quiet > timedelta(0) and now - heard >= quiet


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
        _write_json(self.path, {"sound_at": now.isoformat(timespec="seconds")})
