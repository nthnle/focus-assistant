"""Days written the way people say them: tomorrow, wed, friday, 2026-10-24.

Shared by pausing ("off until monday") and scheduling a priority for later.
"""

from __future__ import annotations

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

