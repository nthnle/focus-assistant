"""Everything a person can do to the state, independent of how they did it.

The CLI, chat, voice and the charger reports all call these, so behaviour
stays identical whichever input was used.
"""

from __future__ import annotations

from datetime import datetime

from . import state as st
from .state import State


def add_priority(s: State, text: str) -> st.Priority:
    text = text.strip()
    if not text:
        raise st.StateError("Priority text is empty")
    p = st.Priority(id=s.next_id, text=text, added_at=st.now_iso())
    s.next_id += 1
    s.priorities.append(p)
    return p


def rename_priority(s: State, priority_id: int, text: str) -> st.Priority:
    text = text.strip()
    if not text:
        raise st.StateError("Priority text is empty")
    p = s.find(priority_id)
    p.text = text
    return p


def complete_priority(s: State, priority_id: int) -> st.Priority:
    p = s.find(priority_id)
    if not p.done:
        p.done_at = st.now_iso()
    return p


def reopen_priority(s: State, priority_id: int) -> st.Priority:
    p = s.find(priority_id)
    p.done_at = None
    return p


def drop_priority(s: State, priority_id: int) -> st.Priority:
    p = s.find(priority_id)
    s.priorities.remove(p)
    return p


def move_priority(s: State, priority_id: int, position: int) -> None:
    """Move to a 1-based position; the top three are what the display shows."""
    p = s.find(priority_id)
    s.priorities.remove(p)
    s.priorities.insert(max(0, position - 1), p)


def log_event(s: State, kind: str, detail: str | None = None,
              at: datetime | None = None) -> dict:
    """`at` backdates the event, for inputs that confirm it after a delay."""
    when = at.astimezone().isoformat(timespec="seconds") if at else st.now_iso()
    event = {"at": when, "kind": kind}
    if detail:
        event["detail"] = detail
    s.events.append(event)
    return event


def report_on_track(s: State) -> dict:
    return log_event(s, st.SELF_REPORT, "on_track")


def report_drifted(s: State) -> dict:
    return log_event(s, st.SELF_REPORT, "drifted")


def pause(s: State, until: datetime) -> dict:
    """Hold check-ins and stretch reminders until `until` (timezone-aware)."""
    s.paused_until = until.isoformat(timespec="seconds")
    same_day = until.date() == datetime.now().astimezone().date()
    return log_event(s, st.PAUSE, until.strftime("%H:%M") if same_day else until.date().isoformat())


def resume(s: State) -> dict:
    s.paused_until = None
    return log_event(s, st.RESUME)
