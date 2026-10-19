"""One scheduled check-in: snapshot state, ask the coach, record, present."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from . import coach, feedback, outputs, pause, pricing, state as st
from .config import Config
from .days import monday_of

log = logging.getLogger(__name__)

SLOTS = ("morning", "midday", "evening")


def slot_for(now: datetime) -> str:
    if now.hour < 11:
        return "morning"
    if now.hour < 16:
        return "midday"
    return "evening"


def run(cfg: Config, slot: str | None = None) -> str | None:
    """Returns the reply, or None when skipped because you are paused for the day."""
    slot = slot or slot_for(datetime.now())
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {SLOTS}")

    # Hold the lock only for the snapshot, not during the network call,
    # so a button press is never stuck waiting on the API.
    with st.locked_state(cfg) as s:
        if pause.day_off(s, datetime.now().astimezone()):
            log.info("Paused %s; skipping the %s check-in", pause.describe(s, datetime.now().astimezone()), slot)
            return None
        payload = coach.build_payload(s, slot, st.now_iso())

    try:
        reply = coach.ask(cfg, payload)
    except coach.CoachError as exc:
        # A check-in still happens without the model: a fixed nudge from the data.
        log.warning("Coach unavailable (%s); using the offline check-in", exc)
        reply = None

    with st.locked_state(cfg) as s:
        if reply is None:
            text, record = coach.offline_reply(s, slot), {"model": "offline"}
        else:
            text, record = reply.text, pricing.priced({
                "model": reply.model,
                "tokens": {"in": reply.input_tokens, "out": reply.output_tokens},
            }, reply.model, reply.input_tokens, reply.output_tokens)
        s.checkins.append({"slot": slot, "at": st.now_iso(), "reply": text, **record})
        if reply is not None:
            _remember_the_week(s, payload, text)
        outputs.deliver(outputs.load(cfg.outputs), s, slot, text)

    if reply is not None:
        feedback.record(cfg, "checkin", payload, reply.text, reply.model)

    return text


def _remember_the_week(s: st.State, payload: dict, text: str) -> None:
    """Keep what a weekly check-in said, so next week's opening can use it.

    Only for replies from the model: an offline check-in leaves the week open
    for the next morning to try again.
    """
    if payload.get("week_start") and s.last_week:
        s.last_week["opened"] = True
    if payload.get("weekly_review"):
        if payload.get("late") and s.last_week:
            s.last_week["review"] = text
        else:
            s.week_review = {"week": monday_of(date.fromisoformat(s.date)).isoformat(), "text": text}


def refresh(cfg: Config, client=None, today: date | None = None) -> str | None:
    """Rewrite today's latest check-in for the list as it is now, after
    priorities were marked done or reopened. For the screen only: it is
    never spoken. Returns the new text, or None when there was nothing to
    rewrite. A CoachError is left to the caller."""
    with st.locked_state(cfg, today=today) as s:
        if not s.checkins:
            return None
        last = s.checkins[-1]
        if last["slot"] == "evening" and date.fromisoformat(s.date).weekday() == 4:
            return None  # Friday's weekly review stays as written
        written, stale = last["at"], last.get("reply")
        payload = coach.build_payload(s, last["slot"], st.now_iso())
        # Not the stale reply itself: given it, the model tends to copy it.
        payload["earlier_checkins_today"] = payload["earlier_checkins_today"][:-1]
        payload["rewrite_of"] = {"written_at": written, "done_since": [
            p.text for p in s.today_list
            if p.done_at and datetime.fromisoformat(p.done_at) > datetime.fromisoformat(written)]}

    reply = coach.ask(cfg, payload, client)

    with st.locked_state(cfg, today=today) as s:
        last = s.checkins[-1] if s.checkins else None
        if last is None or last["at"] != written:
            return None  # a new check-in came in meanwhile; it is newer than this
        if " ".join(reply.text.split()) == " ".join((stale or "").split()):
            log.warning("The rewrite repeated the old check-in; keeping it, with the since-note")
            return None
        last.setdefault("first_reply", last.get("reply"))
        last["reply"] = reply.text
        last["updated_at"] = st.now_iso()
        last.setdefault("rewrites", []).append(pricing.priced({
            "model": reply.model,
            "tokens": {"in": reply.input_tokens, "out": reply.output_tokens},
        }, reply.model, reply.input_tokens, reply.output_tokens))
    feedback.record(cfg, "checkin", payload, reply.text, reply.model)
    return reply.text


class Refresher:
    """Calls `refresh` a few seconds after priorities change. A burst of
    changes (done, then undone with a double press) makes one call, and it
    runs in the background so a voice or chat reply never waits on it."""

    def __init__(self, cfg: Config, delay_s: float = 10.0, rewrite=refresh):
        self.cfg, self.delay_s, self.rewrite = cfg, delay_s, rewrite
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def __call__(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay_s, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            if self.rewrite(self.cfg) is not None:
                log.info("Rewrote the latest check-in for the changed list")
        except Exception as exc:  # the screen keeps the since-note; nothing else depends on this
            log.warning("Couldn't rewrite the check-in: %s", exc)
