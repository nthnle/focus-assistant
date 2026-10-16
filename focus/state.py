"""The local state file: today's priorities, carryovers, check-ins and events.

Every reader and writer goes through `locked_state()`, which holds an
exclusive lock for the whole read-modify-write. That matters once a button
daemon and the check-in timer both write the same file.

Day rollover happens here, in code, not in the model: unfinished priorities
carry over with a counter, finished ones are archived into `history`.
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

SCHEMA_VERSION = 1

# Event kinds the core understands. Hardware adapters may add their own;
# unknown kinds are still stored and shown to the coach.
SELF_REPORT = "self_report"  # detail: "on_track" | "drifted"
SNOOZE = "snooze"
PHONE_OUT = "phone_out"
PHONE_IN = "phone_in"


class StateError(Exception):
    pass


@dataclass
class Priority:
    id: int
    text: str
    done_at: str | None = None
    carried_days: int = 0

    @property
    def done(self) -> bool:
        return self.done_at is not None


@dataclass
class State:
    date: str
    next_id: int = 1
    priorities: list[Priority] = field(default_factory=list)
    checkins: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    @property
    def open_priorities(self) -> list[Priority]:
        return [p for p in self.priorities if not p.done]

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
        )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def roll_over(state: State, today: date, history_days: int) -> bool:
    """Close out a previous day. Returns True if anything changed."""
    if state.date == today.isoformat():
        return False

    event_counts: dict[str, int] = {}
    for e in state.events:
        key = e["kind"] if e["kind"] != SELF_REPORT else f"{SELF_REPORT}:{e.get('detail')}"
        event_counts[key] = event_counts.get(key, 0) + 1

    state.history.append({
        "date": state.date,
        "done": [p.text for p in state.priorities if p.done],
        "open": [p.text for p in state.open_priorities],
        "checkins": len(state.checkins),
        "chats": len(state.chat),
        "events": event_counts,
    })
    state.history = state.history[-history_days:]

    carried = state.open_priorities
    for p in carried:
        p.carried_days += 1
    state.priorities = carried
    state.checkins = []
    state.events = []
    state.chat = []
    state.date = today.isoformat()
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
