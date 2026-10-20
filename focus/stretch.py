"""Stretch reminder: at :50 each hour of a focus block, say a short line.

50 minutes of work, then a 10-minute break. The focus-stretch timer fires at
:50 every weekday hour; this checks FOCUS_BLOCKS and stays quiet outside
them (12:50 lunch, 17:50 after the last block, weekends).

The coach writes the line (FOCUS_CHAT_MODEL, so it is quick and cheap) from
the same data as a check-in, so it can tie the break to what you are doing.
If it hasn't answered within a few seconds, or there is no network, one of
the fixed phrases below is said instead: a reminder always plays at :50.
Each reminder is logged as an event, so the next check-in can see them.

At :00 the focus-back timer says the break is over ("Break's over. Back to
it."), but only after a reminder that actually played at :50, inside a
focus block, and not while you're away or paused. It's a fixed line, so it
needs no model, key or network.

    python -m focus.stretch          what the stretch timer runs
    python -m focus.stretch --now    say one regardless of the time (to try it)
    python -m focus.stretch --back   what the back timer runs
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Callable

import openai

from . import actions, blocks, coach, config, feedback, pause, presence, pricing, state as st
from .config import Config

log = logging.getLogger(__name__)

STRETCH = st.STRETCH_REMINDER
COACH_TIMEOUT = 5.0
# How recent a reminder must be for :00 to count as the end of its break.
BACK_AFTER = timedelta(minutes=15)

SYSTEM_PROMPT = """\
You are the user's focus coach on a small device on their desk. They work \
in 50-minute stretches with a 10-minute break, and 50 minutes are up now. \
You get their priorities, today's check-ins and device events.

Tell them to stand up and move for a few minutes, in one or two short \
sentences, at most 25 words. If the data shows what they have been working \
on, or that they skipped earlier breaks, you may mention it in a few words. \
It will be spoken aloud: plain sentences, no markdown, no emoji. Never \
invent tasks, progress or events."""

PHRASES = [
    "Ten minute break. Stand up and stretch.",
    "Time to get up. Stretch your back and shoulders for a minute.",
    "Break time. Stand, roll your shoulders, and look at something far away.",
    "Fifty minutes done. Get up, walk around, and get some water.",
    "Stand up for a bit. Stretch your arms over your head and loosen your neck.",
    "Pause here. Up out of the chair for a few minutes.",
]


def phrase_for(now: datetime) -> str:
    # By hour, so back-to-back reminders never repeat and each day starts the same.
    return PHRASES[now.hour % len(PHRASES)]


def compose(cfg: Config, now: datetime, client: openai.OpenAI | None = None) -> dict | None:
    """The coach's reminder as {"text", "usage"}, or None if it can't produce one quickly."""
    with st.locked_state(cfg, today=now.date()) as s:
        payload = coach.build_payload(s, "stretch_break", now.astimezone().isoformat(timespec="seconds"))
    try:
        # One short try: a late reminder is worse than a plain one.
        client = client or coach.make_client("the stretch timer gets it from focus.env",
                                             timeout=COACH_TIMEOUT, max_retries=0)
        response = coach.create(
            cfg, client, model=cfg.chat_model, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        text = coach.reply_text(response)
        feedback.record(cfg, "stretch", payload, text, cfg.chat_model)
        return {"text": text,
                "usage": pricing.priced({"model": cfg.chat_model, "in": response.usage.input_tokens,
                                         "out": response.usage.output_tokens},
                                        cfg.chat_model, response.usage.input_tokens,
                                        response.usage.output_tokens)}
    except coach.CoachError as exc:
        log.warning("Coach unavailable (%s); using a fixed phrase", exc)
        return None


def run(cfg: Config, now: datetime, say: Callable[[str], None],
        focus_blocks: list[blocks.Block], force: bool = False,
        write: Callable[[Config, datetime], dict | None] = compose) -> bool:
    """Say the reminder if it is focus time (or forced). Returns whether it did."""
    if not force and not blocks.in_block(now, focus_blocks):
        log.info("Not in a focus block; no reminder")
        return False
    if not force:
        with st.locked_state(cfg, today=now.date()) as s:
            gone = presence.away(cfg, s, now.astimezone())
            quiet = pause.describe(s, now.astimezone())
        if quiet:
            log.info("Paused %s; no reminder", quiet)
            return False
        if gone:
            log.info("Away from the desk; no reminder (you're already up)")
            return False
    written = write(cfg, now)
    text = written["text"] if written else phrase_for(now)
    say(text)
    with st.locked_state(cfg, today=now.date()) as s:
        event = actions.log_event(s, STRETCH, "coach" if written else "fixed", at=now)
        if written:
            event["usage"] = written["usage"]
    log.info("Reminded: %s", text)
    return True


def back_line(s: st.State) -> str:
    n = len(s.open_priorities)
    left = {0: "Your list is clear.", 1: "One thing on your list."}.get(n, f"{n} things on your list.")
    return f"Break's over. Back to it. {left}"


def back(cfg: Config, now: datetime, say: Callable[[str], None],
         focus_blocks: list[blocks.Block]) -> bool:
    """At :00, after a break reminder that played, say it's time to start again."""
    if not blocks.in_block(now, focus_blocks):
        log.info("Not in a focus block; no back-to-work line")
        return False
    aware = now.astimezone()
    s = st.peek(cfg, today=now.date())
    if not any(e["kind"] == STRETCH and aware - datetime.fromisoformat(e["at"]) <= BACK_AFTER
               for e in s.events):
        log.info("No break reminder played at :50; nothing to come back from")
        return False
    quiet = pause.describe(s, aware)
    if quiet:
        log.info("Paused %s; no back-to-work line", quiet)
        return False
    if presence.away(cfg, s, aware):
        log.info("Away from the desk; no back-to-work line")
        return False
    text = back_line(s)
    say(text)
    log.info("Back: %s", text)
    return True


def main(argv: list[str] | None = None) -> int:
    from .outputs import speech

    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    force = "--now" in argv
    now = datetime.now()
    focus_blocks = blocks.from_env()
    if "--back" in argv:
        back(config.load(), now, lambda text: speech.create().say(text), focus_blocks)
        return 0
    if not force and not blocks.in_block(now, focus_blocks):
        log.info("Not in a focus block; no reminder")
        return 0
    # Load Piper only when there is something to say, after the away and
    # pause checks in run(); --now skips those checks too.
    run(config.load(), now, lambda text: speech.create().say(text), focus_blocks, force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
