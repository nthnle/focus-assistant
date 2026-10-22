"""Is anyone at the desk? The desk camera says.

The camera (focus.camera) writes what it sees to camera.json while it runs:
"phone", "work" or "away" (the empty desk), never a picture. You're away
once it has seen the empty desk for FOCUS_AWAY_CAMERA_MIN minutes (5).

When it isn't running, or its model has no "away" label, nothing says
you've gone, so you count as here. The camera runs from 5 minutes before
each focus block to 5 minutes after it, so every check-in and stretch
reminder gets its answer, and it stays on while a held check-in waits for
you (focus.camera).

Away means stretch reminders are skipped and a spoken check-in is held, to
be said when the camera sees you back (`back`).
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

# The camera rewrites camera.json at least every 30 s while it runs; older means it stopped.
CAMERA_FRESH = timedelta(seconds=90)
AWAY = "away"


def _minutes(env: str, default: int) -> timedelta:
    return timedelta(minutes=int(os.environ.get(env, default)))


def camera_path(cfg: Config) -> Path:
    return cfg.state_dir / "camera.json"


def model_path(cfg: Config) -> Path:
    """The camera's model; whether there is one says whether a camera is set up."""
    env = os.environ.get("FOCUS_CAMERA_MODEL")
    return Path(env) if env else cfg.state_dir / "camera" / "model.onnx"


@dataclass(frozen=True)
class Seen:
    """What the desk camera sees: "phone", "work" or "away"."""
    label: str
    since: datetime
    at: datetime          # when it last wrote
    sees_away: bool       # its model knows the empty desk


def record_camera(cfg: Config, label: str, since: datetime, at: datetime, sees_away: bool) -> None:
    """Atomically, so readers never see a half-written file."""
    path = camera_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".camera-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"seen": label, "since": since.isoformat(timespec="seconds"),
                   "at": at.isoformat(timespec="seconds"), "sees_away": sees_away}, f)
    os.replace(tmp, path)


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


def away(cfg: Config, state: State, now: datetime) -> bool:
    """`now` must be timezone-aware, like the timestamps in state."""
    seen = camera_seen(cfg, now)
    return (seen is not None and seen.sees_away and seen.label == AWAY
            and now - seen.since >= _minutes("FOCUS_AWAY_CAMERA_MIN", 5))


def back(cfg: Config, now: datetime) -> bool:
    """Whether a held check-in can be said now: the camera sees someone at
    the desk. Without a camera there's nothing to wait for."""
    seen = camera_seen(cfg, now)
    if seen is None:
        return not model_path(cfg).exists()   # a camera that isn't running yet: wait for it
    return not seen.sees_away or seen.label != AWAY
