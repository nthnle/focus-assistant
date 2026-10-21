"""The local state file: today's priorities, carryovers, check-ins and events.

Every reader and writer goes through `locked_state()`, which holds an
exclusive lock for the whole read-modify-write. That matters once a button
daemon and the check-in timer both write the same file.

Day rollover happens here, in code, not in the model: unfinished priorities
carry over with a counter, finished ones are archived into `history`. A day
counts toward that counter only if a check-in ran on it, so weekends, days
off and planning the next day in the evening don't make a priority look
like it is slipping. Each day's summary also keeps the patterns the coach
needs to suggest changes to your routine: when the phone came out, when
things got done, when check-ins ran.

At the first rollover of a new week, Friday's review moves into
`last_week` for Monday's check-in to pick up.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows dev machines; the Pi always has fcntl
    fcntl = None

from .config import Config
from .days import monday_of

SCHEMA_VERSION = 1

# Event kinds the core understands. Hardware adapters may add their own;
# unknown kinds are still stored and shown to the coach.
SELF_REPORT = "self_report"  # detail: "on_track" | "drifted"
PAUSE = "pause"    # detail: when it ends, "HH:MM" or a date
RESUME = "resume"
PHONE_OUT = "phone_out"
PHONE_IN = "phone_in"
STRETCH_REMINDER = "stretch_reminder"

LATER = "later"  # Priority.on: no date yet


class StateError(Exception):
    pass


@dataclass
class Priority:
    id: int
    text: str
    done_at: str | None = None
    carried_days: int = 0
    added_at: str | None = None
    # Scheduled for later: an ISO date it shows up on, or "later" for some
    # day you pick. Until then it is off today's list and never counts as carried.
    on: str | None = None

    @property
    def done(self) -> bool:
        return self.done_at is not None

    def due(self, day: str) -> bool:
        """On the list for `day` (ISO date). Done ones stay where they were."""
        return self.done or self.on is None or (self.on != LATER and self.on <= day)


@dataclass
class State:
    date: str
    next_id: int = 1
    priorities: list[Priority] = field(default_factory=list)
    checkins: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    # Check-ins and stretch reminders wait until then (focus.pause). Unlike
    # the rest of the day, it survives rollover: a pause can last for days.
    paused_until: str | None = None
    # The week before: {"week", "review", "opened"}, set at the first
    # rollover of a new week. `opened` once Monday's check-in has used it.
    last_week: dict | None = None
    # Friday's wrap-up, once it has run: {"week", "text"}.
    week_review: dict | None = None
    version: int = SCHEMA_VERSION

    @property
    def today_list(self) -> list[Priority]:
        """Today's priorities, done ones included, in their order."""
        return [p for p in self.priorities if p.due(self.date)]

    @property
    def later(self) -> list[Priority]:
        """Scheduled for another day: dated ones first, then "later"."""
        waiting = [p for p in self.priorities if not p.due(self.date)]
        return sorted(waiting, key=lambda p: (p.on == LATER, p.on))

    @property
    def ordered(self) -> list[Priority]:
        """Today's list, then the later ones: how they are numbered everywhere."""
        return self.today_list + self.later

    @property
    def open_priorities(self) -> list[Priority]:
        return [p for p in self.today_list if not p.done]

    @property
    def suggested(self) -> Priority | None:
        """The open item the latest check-in suggested starting with, or the
        only open item when there is just one. The list has no order, so
        this is what "the one" means for the call button."""
        open_ = self.open_priorities
        for c in reversed(self.checkins):
            if c.get("start_id") is not None:
                match = [p for p in open_ if p.id == c["start_id"]]
                if match:
                    return match[0]
                break  # done already; an older suggestion is out of date too
        return open_[0] if len(open_) == 1 else None

    @property
    def done_priorities(self) -> list[Priority]:
        return [p for p in self.priorities if p.done]

    def find(self, priority_id: int) -> Priority:
        for p in self.priorities:
            if p.id == priority_id:
                return p
        raise StateError(f"No priority with id {priority_id}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> State:
        if data.get("version") != SCHEMA_VERSION:
            raise StateError(f"Unsupported state version: {data.get('version')!r}")
        return cls(
            date=data["date"],
            next_id=data.get("next_id", 1),
            priorities=[Priority(**p) for p in data.get("priorities", [])],
            checkins=data.get("checkins", []),
            events=data.get("events", []),
            chat=data.get("chat", []),
            history=data.get("history", []),
            paused_until=data.get("paused_until"),
            # Files from when there were weekly goals keep loading; the goals are dropped.
            last_week={k: v for k, v in data["last_week"].items() if k != "goals"} if data.get("last_week") else None,
            week_review=data.get("week_review"),
        )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def phone_summary(events: list[dict], now: str | None = None) -> dict:
    """Pickups and minutes off the charger from PHONE_OUT / PHONE_IN events.

    With `now`, a phone still out counts up to then; without, only the time
    it was back counts. Malformed or mixed-timezone events stop the totals
    where they got to.
    """
    pickups, minutes, out_at, hours = 0, 0.0, None, {}
    try:
        for e in events:
            if e["kind"] == PHONE_OUT:
                pickups += 1
                out_at = datetime.fromisoformat(e["at"])
                hours[f"{out_at:%H}"] = hours.get(f"{out_at:%H}", 0) + 1
            elif e["kind"] == PHONE_IN and out_at is not None:
                minutes += (datetime.fromisoformat(e["at"]) - out_at).total_seconds() / 60
                out_at = None
        if out_at is not None and now is not None:
            minutes += max(0.0, (datetime.fromisoformat(now) - out_at).total_seconds() / 60)
    except (KeyError, ValueError, TypeError):
        pass
    return {"pickups": pickups, "minutes": round(minutes), "out_since": out_at, "by_hour": hours}


def _hm(iso: str | None) -> str | None:
    try:
        return f"{datetime.fromisoformat(iso):%H:%M}" if iso else None
    except (TypeError, ValueError):
        return None


def roll_over(state: State, today: date, history_days: int) -> bool:
    """Close out a previous day. Returns True if anything changed."""
    if state.date == today.isoformat():
        return False

    event_counts: dict[str, int] = {}
    for e in state.events:
        key = e["kind"] if e["kind"] != SELF_REPORT else f"{SELF_REPORT}:{e.get('detail')}"
        event_counts[key] = event_counts.get(key, 0) + 1
    phone = phone_summary(state.events)

    state.history.append({
        "date": state.date,
        "done": [p.text for p in state.today_list if p.done],
        "open": [p.text for p in state.open_priorities],
        "checkins": len(state.checkins),
        "chats": len(state.chat),
        "events": event_counts,
        # Patterns for routine suggestions.
        "checkin_times": [t for c in state.checkins if (t := _hm(c.get("at")))],
        "done_times": [t for p in state.today_list if p.done and (t := _hm(p.done_at))],
        "phone_pickups": phone["pickups"],
        "phone_minutes_off": phone["minutes"],
        "pickups_by_hour": phone["by_hour"],
    })
    state.history = state.history[-history_days:]

    # The day counts only if a check-in ran on it, and only for priorities
    # that were already there at its last check-in: one added in the evening
    # is tomorrow's plan, not something that slipped.
    last_checkin = state.checkins[-1].get("at") if state.checkins else None
    for p in state.open_priorities:
        if last_checkin and (p.added_at is None or _before(p.added_at, last_checkin)):
            p.carried_days += 1
    state.priorities = [p for p in state.priorities if not p.done]

    old_week = monday_of(date.fromisoformat(state.date))
    if monday_of(today) != old_week:
        review = state.week_review if state.week_review and state.week_review.get("week") == old_week.isoformat() else None
        state.last_week = {
            "week": old_week.isoformat(),
            "review": review["text"] if review else None,
            "opened": False,
        }
        state.week_review = None

    state.checkins = []
    state.events = []
    state.chat = []
    state.date = today.isoformat()
    return True


def _before(a: str, b: str) -> bool:
    """Whether timestamp a is before b; True if either is unreadable, as before."""
    try:
        return datetime.fromisoformat(a) < datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return True


def _read(path: Path, today: date) -> State:
    if not path.exists():
        return State(date=today.isoformat())
    try:
        return State.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StateError(f"{path} is not valid state JSON ({exc}); fix or move it aside") from exc


def _write(path: Path, state: State) -> None:
    # Write-then-rename so a power cut never leaves a half-written file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def peek(cfg: Config, today: date | None = None) -> State:
    """The current (rolled-over) state for a read-only view: no lock, never saved.

    Writes are atomic renames, so a read always sees a whole file.
    """
    today = today or date.today()
    state = _read(cfg.state_path, today)
    roll_over(state, today, cfg.history_days)
    return state


@contextmanager
def locked_state(cfg: Config, today: date | None = None) -> Iterator[State]:
    """Yield the current (rolled-over) state; save it if the block succeeds."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    with open(cfg.lock_path, "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read(cfg.state_path, today)
        roll_over(state, today, cfg.history_days)
        yield state
        _write(cfg.state_path, state)
