"""Everything a person can do to the state, independent of how they did it.

The CLI, chat, voice and the charger reports all call these, so behaviour
stays identical whichever input was used.
"""

from __future__ import annotations

from datetime import date, datetime

from . import state as st
from .days import goal_week
from .state import State


def add_priority(s: State, text: str, on: date | str | None = None) -> st.Priority:
    """`on`: a later day it shows up on, or "later"; None for today."""
    text = text.strip()
    if not text:
        raise st.StateError("Priority text is empty")
    p = st.Priority(id=s.next_id, text=text, added_at=st.now_iso())
    s.next_id += 1
    s.priorities.append(p)
    if on is not None:
        schedule_priority(s, p.id, on)
    return p


def schedule_priority(s: State, priority_id: int, on: date | str | None) -> st.Priority:
    """Move a priority to a later day, to "later", or (None) back to today."""
    p = s.find(priority_id)
    if p.done:
        raise st.StateError(f"{p.text} is already done")
    if on is None:
        if p.on is not None:
            p.on = None
            p.added_at = st.now_iso()  # back today: it hasn't slipped yet
        return p
    if isinstance(on, date):
        if on.isoformat() <= s.date:
            raise st.StateError("Pick a day after today, or bring it back to today instead")
        on = on.isoformat()
    elif on != st.LATER:
        raise st.StateError(f"Can't schedule for {on!r}")
    p.on = on
    p.carried_days = 0
    # Put it with the later ones, so today's numbering doesn't shift around it.
    s.priorities.remove(p)
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


MAX_GOALS = 3


def add_goal(s: State, text: str) -> dict:
    """One of this week's goals, or next week's on a weekend. They move out
    when their week is over."""
    text = text.strip()
    if not text:
        raise st.StateError("Goal text is empty")
    week = goal_week(date.fromisoformat(s.date)).isoformat()
    if sum(1 for g in s.goals if g.get("week", week) == week) >= MAX_GOALS:
        raise st.StateError(f"Keep it to {MAX_GOALS} goals a week; drop one first")
    goal = {"id": max((g["id"] for g in s.goals), default=0) + 1, "text": text, "done_at": None, "week": week}
    s.goals.append(goal)
    return goal


def complete_goal(s: State, goal_id: int) -> dict:
    goal = s.find_goal(goal_id)
    goal["done_at"] = goal["done_at"] or st.now_iso()
    return goal


def drop_goal(s: State, goal_id: int) -> dict:
    goal = s.find_goal(goal_id)
    s.goals.remove(goal)
    return goal
