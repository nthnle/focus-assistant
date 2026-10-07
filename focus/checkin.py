"""One scheduled check-in: snapshot state, ask the coach, record, present."""

from __future__ import annotations

from datetime import datetime

from . import coach, outputs, state as st
from .config import Config

SLOTS = ("morning", "midday", "evening")


def slot_for(now: datetime) -> str:
    if now.hour < 11:
        return "morning"
    if now.hour < 16:
        return "midday"
    return "evening"


def run(cfg: Config, slot: str | None = None) -> str:
    slot = slot or slot_for(datetime.now())
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {SLOTS}")

    # Hold the lock only for the snapshot, not during the network call,
    # so a button press is never stuck waiting on the API.
    with st.locked_state(cfg) as s:
        payload = coach.build_payload(s, slot, st.now_iso())

    reply = coach.ask(cfg, payload)

    with st.locked_state(cfg) as s:
        s.checkins.append({
            "slot": slot,
            "at": st.now_iso(),
            "reply": reply.text,
            "model": reply.model,
            "tokens": {"in": reply.input_tokens, "out": reply.output_tokens},
        })
        outputs.deliver(outputs.load(cfg.outputs), s, slot, reply.text)

    return reply.text
