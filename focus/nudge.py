"""Phone nudge: on the phone for 5 minutes of focus time, and the coach says so.

The desk camera (focus.camera) logs when you pick the phone up in a focus
block. Between turns, every 30 seconds, the voice service asks this module
whether a nudge is due: once you've been on it for FOCUS_PHONE_NUDGE_MIN
minutes (5; 0 turns nudges off), it says a short line, and again every 5
minutes while you stay on it. Putting the phone down for under 2 minutes
doesn't restart the count: to the nudges, that's the same stretch on the
phone. Not while paused, and not in the break after a stretch reminder,
when the phone is fair game.

The coach writes the line (FOCUS_CHAT_MODEL, quick and cheap) from the same
data as a check-in, so it can point you back at something on your list. If
it hasn't answered within a few seconds, a fixed line is said instead. Each
nudge is logged as an event, so check-ins can see them.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Callable

import openai

from . import actions, blocks, coach, pause, presence, state as st, stretch
from .config import Config

log = logging.getLogger(__name__)

COACH_TIMEOUT = 5.0
JOIN = timedelta(minutes=2)   # a put-down shorter than this doesn't end a stretch on the phone

SYSTEM_PROMPT = """\
You are the user's focus coach on a small device on their desk. Its camera \
has seen them on their phone for `minutes_on_phone` minutes of focus time. \
You get their to-do list, today's check-ins and device events.

In one or two short sentences, at most 25 words, tell them how long it has \
been and point them back to work, naming one open item if there is one. No \
guilt and no lecture. If `nudge` is more than 1, they kept going after an \
earlier nudge: be shorter and firmer. It will be spoken aloud: plain \
sentences, no markdown, no emoji. Never invent tasks, progress or events."""


def every() -> int:
    return int(os.environ.get("FOCUS_PHONE_NUDGE_MIN", "5"))


def stretch_since(events: list[dict]) -> datetime | None:
    """When the current stretch on the phone began, if it's in use now: the
    open pickup's start, or an earlier one's if the phone was only put down
    for under JOIN in between."""
    start = put_down = None
    in_use = False
    for e in events:
        if e["kind"] == st.PHONE_OUT:
            at = datetime.fromisoformat(e["at"])
            if start is None or put_down is None or at - put_down > JOIN:
                start = at
            in_use = True
        elif e["kind"] == st.PHONE_IN:
            put_down = datetime.fromisoformat(e["at"])
            in_use = False
    return start if in_use else None


def due(s: st.State, now: datetime, every_min: int) -> tuple[int, int] | None:
    """(minutes on the phone, which nudge this would be), if one is due now."""
    since = stretch_since(s.events)
    if since is None or every_min <= 0:
        return None
    minutes = int((now - since).total_seconds() // 60)
    given = sum(1 for e in s.events
                if e["kind"] == st.PHONE_NUDGE and datetime.fromisoformat(e["at"]) >= since)
    if minutes < every_min * (given + 1):
        return None
    return minutes, given + 1


def fixed_line(s: st.State, minutes: int) -> str:
    item = s.suggested or (s.open_priorities[0] if s.open_priorities else None)
    back = f"Back to {item.text}." if item else "Time to put it down."
    return f"You've been on your phone for {minutes} minutes. {back}"


def compose(cfg: Config, now: datetime, minutes: int, n: int,
            client: openai.OpenAI | None = None) -> dict | None:
    """The coach's nudge as {"text", "usage"}, or None if it can't produce one quickly."""
    payload = coach.build_payload(st.peek(cfg, today=now.date()), "phone_nudge",
                                  now.isoformat(timespec="seconds"))
    payload["minutes_on_phone"] = minutes
    payload["nudge"] = n
    return coach.short_reply(cfg, "nudge", SYSTEM_PROMPT, payload,
                             "the voice service gets it from focus.env", client, COACH_TIMEOUT)


def run(cfg: Config, now: datetime, say: Callable[[str], None], focus_blocks: list[blocks.Block],
        write: Callable[[Config, datetime, int, int], dict | None] = compose) -> bool:
    """Say a nudge if one is due. `now` must be timezone-aware. Returns whether it did."""
    if not blocks.in_block(now, focus_blocks):
        return False
    if presence.camera_seen(cfg, now) is None:
        return False  # the camera isn't watching: an open session may be stale
    s = st.peek(cfg, today=now.date())
    if pause.paused(s, now) or stretch.on_break(s, now):
        return False
    found = due(s, now, every())
    if found is None:
        return False
    minutes, n = found
    written = write(cfg, now, minutes, n)
    text = written["text"] if written else fixed_line(s, minutes)
    # Logged before it's said: if the speaker fails, the next try waits for
    # the next nudge instead of asking the model again every 30 s.
    with st.locked_state(cfg, today=now.date()) as s:
        event = actions.log_event(s, st.PHONE_NUDGE, f"{minutes} min", at=now)
        if written:
            event["usage"] = written["usage"]
    log.info("Phone nudge %d after %d min: %s", n, minutes, text)
    say(text)
    return True
