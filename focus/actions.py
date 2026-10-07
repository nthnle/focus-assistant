"""Everything a person can do to the state, independent of how they did it.

The CLI calls these today. Later, the Inky buttons, the voice intent parser
and the phone-dock switch call the same functions, so behaviour stays
identical whichever input was used.
"""

from __future__ import annotations

from . import state as st
from .state import State


def add_priority(s: State, text: str) -> st.Priority:
    text = text.strip()
    if not text:
        raise st.StateError("Priority text is empty")
    p = st.Priority(id=s.next_id, text=text)
    s.next_id += 1
    s.priorities.append(p)
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


def log_event(s: State, kind: str, detail: str | None = None) -> dict:
    event = {"at": st.now_iso(), "kind": kind}
    if detail:
        event["detail"] = detail
    s.events.append(event)
    return event


def report_on_track(s: State) -> dict:
    return log_event(s, st.SELF_REPORT, "on_track")


def report_drifted(s: State) -> dict:
    return log_event(s, st.SELF_REPORT, "drifted")
