"""Is anyone at the desk? The desk camera says.

The camera (focus.camera) writes what it sees to camera.json while it runs:
"phone", "work" or "away" (the empty desk), never a picture. You're away
once it has seen the empty desk for FOCUS_AWAY_CAMERA_MIN minutes (5).

When it isn't running, or its model has no "away" label, nothing says
you've gone, so you count as here. Off on schedule, it still writes "off"
every 30 s, so a camera that is merely off can be told from a service that
has stopped. The camera runs from 5 minutes before each focus block to 5
minutes after it, so every check-in and stretch reminder gets its answer,
and it stays on while a held check-in waits for you (focus.camera).

Away means stretch reminders are skipped and a spoken check-in is held, to
be said when the camera sees you back (`back`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import state as st
from .config import Config

# The camera rewrites camera.json at least every 30 s while it runs; older means it stopped.
CAMERA_FRESH = timedelta(seconds=90)
AWAY = "away"
PHONE = "phone"
OFF = "off"        # the service runs, the camera is off (outside its hours, or paused)


def camera_path(cfg: Config) -> Path:
    return cfg.state_dir / "camera.json"


@dataclass(frozen=True)
class Seen:
    """What the desk camera sees: "phone", "work", "away", or "off"."""
    label: str
    since: datetime
    at: datetime          # when it last wrote
    sees_away: bool       # its model knows the empty desk

    @property
    def looking(self) -> bool:
        return self.label != OFF

    @property
    def empty_desk(self) -> bool:
        return self.sees_away and self.label == AWAY


def record_camera(cfg: Config, label: str, since: datetime, at: datetime, sees_away: bool) -> None:
    # Gone with the next boot anyway, so no fsync: it's rewritten every 30 s.
    st.write_json(camera_path(cfg), {"seen": label, "since": since.isoformat(timespec="seconds"),
                                     "at": at.isoformat(timespec="seconds"), "sees_away": sees_away},
                  durable=False)


def clear_camera(cfg: Config) -> None:
    camera_path(cfg).unlink(missing_ok=True)


def read_camera(cfg: Config) -> Seen | None:
    """camera.json as last written, however long ago."""
    try:
        data = json.loads(camera_path(cfg).read_text())
        return Seen(str(data["seen"]), datetime.fromisoformat(data["since"]),
                    datetime.fromisoformat(data["at"]), bool(data.get("sees_away")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest(cfg: Config, now: datetime) -> Seen | None:
    """camera.json if the service wrote it lately; None means it isn't running."""
    seen = read_camera(cfg)
    return seen if seen is not None and now - seen.at <= CAMERA_FRESH else None


def camera_seen(cfg: Config, now: datetime) -> Seen | None:
    """What the camera sees now, or None when it isn't looking."""
    seen = latest(cfg, now)
    return seen if seen is not None and seen.looking else None


def away(cfg: Config, now: datetime) -> bool:
    """`now` must be timezone-aware, like the timestamps in state."""
    seen = camera_seen(cfg, now)
    wait = timedelta(minutes=int(os.environ.get("FOCUS_AWAY_CAMERA_MIN", "5")))
    return seen is not None and seen.empty_desk and now - seen.since >= wait


def back(cfg: Config, now: datetime) -> bool:
    """Whether a held check-in can be said now: the camera sees someone at
    the desk. Without a running camera service there's nothing to wait for,
    so it can; with the camera off for now, it waits for it to come on (it
    does, while a check-in is held)."""
    seen = latest(cfg, now)
    return seen is None or (seen.looking and not seen.empty_desk)


def at_desk(cfg: Config, now: datetime, settle: timedelta) -> bool:
    """The camera has seen someone at the desk (working or on the phone) for
    at least `settle`. False without a camera that knows the empty desk:
    nothing then shows anyone sat down."""
    seen = camera_seen(cfg, now)
    return (seen is not None and seen.sees_away and seen.label != AWAY
            and now - seen.since >= settle)


def phone_in_use_since(cfg: Config, state: st.State, now: datetime) -> datetime | None:
    """When the phone was picked up, while the camera still sees it in use.
    An open session counts only while the camera is looking: one left open
    by a camera that died says nothing about now."""
    if camera_seen(cfg, now) is None:
        return None
    return st.phone_in_use_since(state.events)
