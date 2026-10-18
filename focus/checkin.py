"""One scheduled check-in: snapshot state, ask the coach, record, present."""

from __future__ import annotations

import logging
from datetime import datetime

from . import coach, outputs, pause, pricing, state as st
from .config import Config

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
        outputs.deliver(outputs.load(cfg.outputs), s, slot, text)

    return text
