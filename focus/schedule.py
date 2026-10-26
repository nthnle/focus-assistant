"""When check-ins come: once you sit down at the desk, on any day.

focus-checkin.timer runs this every 5 minutes (focus-checkin-tick.service,
`python -m focus.schedule`); `sudo systemctl start focus-checkin` still runs
one straight away.
It is light on purpose: it loads the coach only when a check-in is due.

- A check-in slot opens at its time (FOCUS_CHECKINS, "09:00,13:00,17:30" for
  morning, midday and evening). Each runs at most once a day, and arriving
  late gets only the latest one, never a backlog.
- At least GAP after the previous check-in, so sitting down at 12:50 doesn't
  bring the midday one ten minutes after the morning one.
- Not after FOCUS_CHECKIN_UNTIL (20:00), and never while paused: a check-in
  due during a pause comes once it's over.
- With the desk camera (a model that knows the empty desk), it comes once
  the camera has seen you at the desk for SETTLE, and is written then, so
  it's about the day as it is. The camera stays on while one is due
  (focus.camera).
- Without one, nothing shows you sat down: on a work day the check-in comes
  at its time, as with a plain clock; on a day off there is none.

On a day off (FOCUS_WORKDAYS, focus.days) the check-in is short, and about
what you planned for that day (focus.coach).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from . import config, days, pause, presence, state as st
from .config import Config
from .state import State

log = logging.getLogger(__name__)

SLOTS = ("morning", "midday", "evening")
DEFAULT_TIMES = "09:00,13:00,17:30"
DEFAULT_UNTIL = "20:00"
GAP = timedelta(hours=1)
SETTLE = timedelta(minutes=2)


@dataclass(frozen=True)
class Times:
    opens: tuple[time, ...]   # when morning, midday and evening open
    until: time               # no check-in from then on


def from_env() -> Times:
    """FOCUS_CHECKINS and FOCUS_CHECKIN_UNTIL. A bad value raises."""
    try:
        opens = tuple(time.fromisoformat(t.strip())
                      for t in os.environ.get("FOCUS_CHECKINS", DEFAULT_TIMES).split(","))
        until = time.fromisoformat(os.environ.get("FOCUS_CHECKIN_UNTIL", DEFAULT_UNTIL).strip())
    except ValueError as exc:
        raise ValueError(f"FOCUS_CHECKINS / FOCUS_CHECKIN_UNTIL: {exc}") from exc
    if len(opens) != len(SLOTS) or list(opens) != sorted(set(opens)) or until <= opens[-1]:
        raise ValueError("FOCUS_CHECKINS: three times in order, like 09:00,13:00,17:30, "
                         "all before FOCUS_CHECKIN_UNTIL")
    return Times(opens, until)


def due(state: State, now: datetime, times: Times | None) -> str | None:
    """The check-in slot waiting at `now` (timezone-aware), or None."""
    if times is None or pause.paused(state, now):
        return None
    t = now.time()
    if not times.opens[0] <= t < times.until:
        return None
    slot = [name for name, opens in zip(SLOTS, times.opens) if t >= opens][-1]
    if any(c["slot"] == slot for c in state.checkins):
        return None
    if state.checkins and now - datetime.fromisoformat(state.checkins[-1]["at"]) < GAP:
        return None
    return slot


def can_see_you(cfg: Config, now: datetime) -> bool:
    """The camera service runs, with a model that knows the empty desk."""
    seen = presence.latest(cfg, now)
    return seen is not None and seen.sees_away


def what_now(cfg: Config, now: datetime, times: Times) -> str | None:
    """The check-in to run now, if any."""
    slot = due(st.peek(cfg, today=now.date()), now, times)
    if slot is None:
        return None
    if can_see_you(cfg, now):
        return slot if presence.at_desk(cfg, now, SETTLE) else None
    return slot if days.is_workday(now.date()) else None


def waiting_line(cfg: Config, state: State, now: datetime, times: Times) -> str:
    """For `focus status`: what the next check-in waits for."""
    slot = due(state, now, times)
    if slot is None:
        later = [o for o in times.opens if o > now.time()]
        if pause.paused(state, now):
            return "waiting for the pause to end"
        return f"next one opens at {later[0]:%H:%M}" if later else "none left today"
    if can_see_you(cfg, now):
        return f"{slot} due: it comes once you sit down at the desk"
    if days.is_workday(now.date()):
        return f"{slot} due: it comes within 5 minutes"
    return "none on a day off without the desk camera"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    now = datetime.now().astimezone()
    slot = what_now(cfg, now, from_env())
    if slot is None:
        return 0
    from . import checkin   # the coach and its client: only when there's a check-in to write
    log.info("Running the %s check-in", slot)
    checkin.run(cfg, slot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
