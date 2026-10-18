"""Pausing: "quiet until 11" for a meeting, or "pause until Monday" for days off.

`State.paused_until` is when things start again. While paused:

- stretch reminders are skipped;
- a check-in on a day you are paused through (a day off) is skipped
  entirely: no model call, and the day doesn't count toward carried days;
- a check-in during a shorter pause still runs and shows on the screen, but
  isn't spoken; the voice service says it once the pause is over, as it does
  when you come back to the desk.

Set it with `focus pause`, or by asking the coach through chat or voice.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from .state import State

MAX_DAYS = 60
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
HELP = "say when: 11:00, 2pm, 45m, 2h, tomorrow, monday or 2026-10-24"

_DURATION = re.compile(r"(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)")
_CLOCK = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_until(text: str, now: datetime) -> datetime:
    """When a pause written like `HELP` ends. `now` must be timezone-aware.

    A day (tomorrow, a weekday, a date) means "back on that day": the pause
    ends at its midnight.
    """
    t = " ".join(text.strip().lower().split()).removeprefix("until ").removeprefix("till ")
    if not t:
        raise ValueError(f"No end given; {HELP}")
    today = now.date()
    if m := _DURATION.fullmatch(t):
        amount, unit = int(m[1]), m[2]
        until = now + (timedelta(hours=amount) if unit.startswith("h") else timedelta(minutes=amount))
    elif t == "tomorrow":
        until = _midnight(today + timedelta(days=1), now)
    elif (day := next((d for d in WEEKDAYS if len(t) >= 3 and d.startswith(t)), None)) is not None:
        ahead = (WEEKDAYS.index(day) - today.weekday()) % 7 or 7
        until = _midnight(today + timedelta(days=ahead), now)
    elif _DATE.fullmatch(t):
        until = _midnight(datetime.strptime(t, "%Y-%m-%d").date(), now)
    elif m := _CLOCK.fullmatch(t):
        hour, minute, half = int(m[1]), int(m[2] or 0), m[3]
        if half and not 1 <= hour <= 12 or hour > 23 or minute > 59:
            raise ValueError(f"{text!r} is not a time of day; {HELP}")
        if half:
            hour = hour % 12 + (12 if half == "pm" else 0)
        until = datetime.combine(today, time(hour, minute), now.tzinfo)
    else:
        raise ValueError(f"Can't tell when {text!r} is; {HELP}")
    if until <= now:
        raise ValueError(f"{text!r} has already passed; {HELP}")
    if until - now > timedelta(days=MAX_DAYS):
        raise ValueError(f"That's more than {MAX_DAYS} days away")
    return until


def _midnight(day, now: datetime) -> datetime:
    return datetime.combine(day, time(0, 0), now.tzinfo)


def until(s: State) -> datetime | None:
    return datetime.fromisoformat(s.paused_until) if s.paused_until else None


def paused(s: State, now: datetime) -> bool:
    """`now` must be timezone-aware, like the timestamps in state."""
    end = until(s)
    return end is not None and now < end


def day_off(s: State, now: datetime) -> bool:
    """Paused for the rest of today: check-ins are skipped, not just kept quiet."""
    end = until(s)
    return end is not None and now < end and end.date() > now.date()


def describe(s: State, now: datetime) -> str | None:
    """"until 11:00", "until Mon 27 Oct", or None when not paused."""
    end = until(s)
    if end is None or now >= end:
        return None
    if end.date() == now.date():
        return f"until {end:%H:%M}"
    return f"until {end:%a %d %b}" + ("" if end.time() == time(0, 0) else f" {end:%H:%M}")
