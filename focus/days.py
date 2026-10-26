"""Days written the way people say them: tomorrow, wed, friday, 2026-10-24.

Shared by pausing ("off until monday") and scheduling a priority for later.
"""

from __future__ import annotations

import functools
import os
import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_day(text: str, today: date) -> date | None:
    """The day `text` names, always after today; None if it names no day.

    A weekday means the next one: on a Monday, "monday" is a week away.
    """
    t = " ".join(text.strip().lower().split())
    if t == "tomorrow":
        return today + timedelta(days=1)
    if len(t) >= 3 and (day := next((d for d in WEEKDAYS if d.startswith(t)), None)):
        return today + timedelta(days=(WEEKDAYS.index(day) - today.weekday()) % 7 or 7)
    if _DATE.fullmatch(t):
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    return None


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())



# --- work days -------------------------------------------------------------
# FOCUS_WORKDAYS: the days with focus blocks, stretch reminders, phone counting
# and carried-day counts, e.g. "mon-fri" (the default), "sun-thu" or
# "mon,tue,thu". Check-ins come on every day; on the others they are short
# and about what was planned for that day (focus.coach).

DEFAULT_WORKDAYS = "mon-fri"


def _weekday(name: str) -> int:
    name = name.strip().lower()
    match = [i for i, d in enumerate(WEEKDAYS) if len(name) >= 3 and d.startswith(name)]
    if not match:
        raise ValueError(f"FOCUS_WORKDAYS: {name!r} isn't a day (mon, tue, ...)")
    return match[0]


@functools.lru_cache(maxsize=8)
def parse_workdays(text: str) -> frozenset[int]:
    """Weekday numbers (Monday 0) from "mon-fri", "sun-thu" or "mon,wed,fri"."""
    found: set[int] = set()
    for part in text.split(","):
        if not part.strip():
            continue
        first, _, last = part.partition("-")
        a = _weekday(first)
        b = _weekday(last) if last else a
        found.update((a + i) % 7 for i in range((b - a) % 7 + 1))   # sun-thu wraps
    if not found:
        raise ValueError("FOCUS_WORKDAYS: name at least one day")
    return frozenset(found)


def workdays() -> frozenset[int]:
    return parse_workdays(os.environ.get("FOCUS_WORKDAYS", DEFAULT_WORKDAYS))


def is_workday(day: date) -> bool:
    return day.weekday() in workdays()


def next_workday(day: date) -> date:
    """The first work day after `day`."""
    day += timedelta(days=1)
    while not is_workday(day):
        day += timedelta(days=1)
    return day


def last_workday_of_week(day: date) -> bool:
    """`day` is a work day and the week (Monday to Sunday) has none after it:
    its evening check-in is the week's wrap-up."""
    return is_workday(day) and all(not is_workday(day + timedelta(days=i))
                                   for i in range(1, 7 - day.weekday()))


def days_off_since(day: date) -> date:
    """The first day of the run of days off that `day` is in: Saturday, on a
    Sunday with the usual week. There is always a work day within six days back."""
    while not is_workday(day - timedelta(days=1)):
        day -= timedelta(days=1)
    return day
