"""The single Anthropic API call that turns state into a short coaching reply."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anthropic

from .config import Config
from .state import State

# Kept byte-stable so it can be prompt-cached if it ever grows past the
# minimum cacheable size. Per-run data goes in the user message, not here.
SYSTEM_PROMPT = """\
You are a focus coach on a small device on the user's desk. Three times a \
day you get their priorities, what they have done, and what the device has \
logged (self-reports, snoozes, phone taken off the dock during focus time). \
Reply with what helps them do the right next thing.

The reply may be shown on a small e-ink screen or spoken aloud, so write \
plain sentences: no markdown, no lists, no emoji, at most 70 words.

By check-in:
- morning: name the one priority to start with and why. If there are more \
than three open priorities, or something has carried over for three or more \
days, say so and suggest dropping or shrinking it.
- midday: compare progress against the morning plan. If they drifted or the \
phone came out often, name it plainly once, without guilt, and give one \
concrete reset.
- evening: acknowledge what got done, name what carries over, and suggest \
the first thing for tomorrow. Encourage putting the phone away for the night.

If there are no priorities, ask them to set up to three, in one sentence.
Be warm and direct. Do not invent tasks, progress or events that are not in \
the data."""


@dataclass
class CoachReply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class CoachError(Exception):
    pass


def build_payload(s: State, slot: str, now: str) -> dict:
    return {
        "checkin": slot,
        "now": now,
        "priorities": [
            {
                "id": p.id,
                "text": p.text,
                "done": p.done,
                "done_at": p.done_at,
                "carried_days": p.carried_days,
            }
            for p in s.priorities
        ],
        "earlier_checkins_today": [
            {"slot": c["slot"], "at": c["at"], "reply": c.get("reply")} for c in s.checkins
        ],
        "events_today": s.events,
        "recent_days": s.history[-5:],
    }


def ask(cfg: Config, payload: dict, client: anthropic.Anthropic | None = None) -> CoachReply:
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise CoachError(
                "ANTHROPIC_API_KEY is not set; check-ins get it from the systemd unit "
                "(run one with: sudo systemctl start focus-checkin)"
            )
        client = anthropic.Anthropic(timeout=60.0, max_retries=3)
    try:
        response = client.beta.messages.create(
            model=cfg.model,
            max_tokens=4000,
            output_config={"effort": cfg.effort},
            # If a safety classifier ever declines, re-run on Anthropic's
            # recommended fallback model instead of returning nothing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError as exc:
        raise CoachError("API key rejected; check /etc/focus-assistant/focus.env") from exc
    except anthropic.RateLimitError as exc:
        raise CoachError("Rate limited by the API, even after retries") from exc
    except anthropic.APIStatusError as exc:
        raise CoachError(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise CoachError("Could not reach the API; is the network up?") from exc

    if response.stop_reason == "refusal":
        raise CoachError("The coach declined to answer this check-in")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise CoachError(f"Empty reply (stop_reason={response.stop_reason})")

    return CoachReply(
        text=text,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
