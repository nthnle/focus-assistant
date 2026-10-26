"""The coach's replies, kept with what it was given, plus your ratings.

Every check-in, chat or voice reply, stretch reminder and phone nudge the model writes is
appended to `replies.jsonl` in the state folder, next to the exact payload
it was given, so any reply can be looked at and reproduced later. Each one
is run through `check()`: rules a reply must follow that code can verify
(length, no markdown, a morning check-in names a priority, a short reply on
a weekend). `focus rate good|bad "why"`, or telling the coach "that was
wrong", adds a rating to the latest reply. `focus feedback` lists what was
rated bad or failed a check, to bring into the next round of improvements.

The log never leaves the Pi. Entries older than KEEP_DAYS are dropped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows dev machines
    fcntl = None

from .config import Config

KEEP_DAYS = 60

# Word limits from the prompts, with a little slack.
LIMITS = {"checkin": 70, "weekly": 100, "chat": 60, "voice": 30, "stretch": 25, "nudge": 25}
SLACK = 1.2

_MARKDOWN = re.compile(r"(\*\*|__|`|^\s*#|^\s*[-*•]\s|^\s*\d+[.)]\s)", re.M)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_WORD = re.compile(r"[a-z0-9']+")


def path(cfg: Config) -> Path:
    return cfg.state_dir / "replies.jsonl"


@contextmanager
def _locked(cfg: Config) -> Iterator[None]:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "replies.lock", "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(cfg: Config, entry: dict) -> None:
    with _locked(cfg):
        with open(path(cfg), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _limit(kind: str, payload: dict) -> int:
    if kind == "checkin" and (payload.get("weekly_review") or payload.get("week_start")):
        return LIMITS["weekly"]
    return LIMITS.get(kind, LIMITS["checkin"])


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 4}


def check(kind: str, payload: dict, reply: str) -> list[str]:
    """What's wrong with a reply, as far as code can tell. Empty: nothing found."""
    problems = []
    text = reply.strip()
    if not text:
        return ["empty reply"]
    count = len(text.split())
    limit = _limit(kind, payload)
    if count > limit * SLACK:
        problems.append(f"{count} words, over the {limit}-word limit")
    if _MARKDOWN.search(text):
        problems.append("markdown in a reply meant for a screen or a speaker")
    if _EMOJI.search(text):
        problems.append("emoji")
    facts = payload.get("facts", {})
    # Older payloads only have the weekday's name.
    day_off = not facts.get("workday", facts.get("weekday") not in ("Saturday", "Sunday"))
    if kind == "checkin" and day_off and count > 45:
        problems.append("long for a day-off check-in (asked for two sentences)")
    if kind == "checkin" and payload.get("checkin") == "morning" and not day_off:
        open_ = [p["text"] for p in payload.get("priorities", []) if not p.get("done")]
        said = _words(text)
        if open_ and not any(_words(p) & said for p in open_):
            problems.append("morning check-in doesn't suggest any open item")
    return problems


def record(cfg: Config, kind: str, payload: dict, reply: str, model: str) -> str:
    """Log one reply from the model; returns its id. Never raises: losing a
    log line must not lose the reply."""
    entry = {
        "id": uuid.uuid4().hex[:8],
        "at": _now(),
        "kind": kind,
        "slot": payload.get("checkin"),
        "model": model,
        "reply": reply,
        "checks": check(kind, payload, reply),
        "payload": payload,
    }
    try:
        _prune(cfg)
        _append(cfg, entry)
    except OSError:
        pass
    return entry["id"]


def entries(cfg: Config) -> list[dict]:
    """Replies with their ratings attached, oldest first."""
    try:
        lines = path(cfg).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    replies, ratings = [], []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        (ratings if item.get("type") == "rating" else replies).append(item)
    by_id = {r["id"]: r for r in replies}
    for rating in ratings:
        if rating.get("reply") in by_id:
            by_id[rating["reply"]]["rating"] = {k: rating.get(k) for k in ("value", "note", "at")}
    return replies


def rate(cfg: Config, value: str, note: str = "") -> dict:
    """Rate the latest reply "good" or "bad". Returns the reply rated."""
    if value not in ("good", "bad"):
        raise ValueError("rate it good or bad")
    replies = entries(cfg)
    if not replies:
        raise ValueError("No replies recorded yet")
    last = replies[-1]
    _append(cfg, {"type": "rating", "reply": last["id"], "value": value,
                  "note": note.strip()[:500], "at": _now()})
    return last


def _prune(cfg: Config) -> None:
    """Drop entries older than KEEP_DAYS; cheap when there are none."""
    p = path(cfg)
    try:
        first = p.open(encoding="utf-8").readline()
        oldest = datetime.fromisoformat(json.loads(first)["at"])
    except (OSError, ValueError, KeyError):
        return
    cutoff = datetime.now().astimezone() - timedelta(days=KEEP_DAYS)
    if oldest >= cutoff:
        return
    with _locked(cfg):
        keep = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                if datetime.fromisoformat(json.loads(line)["at"]) >= cutoff:
                    keep.append(line)
            except (ValueError, KeyError):
                continue
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".replies-", suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in keep))
        os.replace(tmp, p)


def report(cfg: Config, days: int = 14) -> str:
    """What to look at: replies rated bad or failing a check, newest first."""
    since = datetime.now().astimezone() - timedelta(days=days)
    recent = [e for e in entries(cfg) if datetime.fromisoformat(e["at"]) >= since]
    flagged = [e for e in recent if e.get("checks") or (e.get("rating") or {}).get("value") == "bad"]
    good = sum(1 for e in recent if (e.get("rating") or {}).get("value") == "good")
    lines = [f"Last {days} days: {len(recent)} replies, {len(flagged)} to look at, {good} rated good."]
    for e in reversed(flagged):
        when = datetime.fromisoformat(e["at"]).strftime("%a %d %b %H:%M")
        what = e["kind"] + (f" ({e['slot']})" if e.get("slot") and e["kind"] == "checkin" else "")
        lines += ["", f"{when}  {what}  [{e['id']}]", f"  {e['reply']}"]
        rating = e.get("rating") or {}
        if rating.get("value") == "bad":
            lines.append("  rated bad" + (f": {rating['note']}" if rating.get("note") else ""))
        for problem in e.get("checks", []):
            lines.append(f"  check: {problem}")
    if not flagged:
        lines.append("Nothing flagged.")
    lines += ["", f"Full entries, with what the coach was given: {path(cfg)}"]
    return "\n".join(lines)


def summary(cfg: Config, days: int = 14) -> str:
    """One line for `focus status`."""
    since = datetime.now().astimezone() - timedelta(days=days)
    recent = [e for e in entries(cfg) if datetime.fromisoformat(e["at"]) >= since]
    checks = sum(1 for e in recent if e.get("checks"))
    bad = sum(1 for e in recent if (e.get("rating") or {}).get("value") == "bad")
    return f"{len(recent)} in {days} days, {checks} failed a check, {bad} rated bad"
