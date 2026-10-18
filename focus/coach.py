"""The model call that turns state into a short coaching reply.

The model is an open model on any OpenAI-compatible host (Groq, Together,
OpenRouter, Fireworks, DeepInfra, a local Ollama...), set in focus.env:

    FOCUS_LLM_BASE_URL   e.g. https://api.groq.com/openai/v1
    FOCUS_LLM_API_KEY    the provider's key
    FOCUS_MODEL          the provider's name for the model

`create()` turns the provider's answer into a small `Response`, so callers
(check-ins, chat, voice, stretch) never see the wire format. Some open
models think out loud in <think>...</think> before answering; that is
stripped here, streamed or not, so it never reaches the screen or speaker.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import openai

from . import pause, state as st
from .config import Config
from .state import State

# Kept byte-stable so providers that cache prompts can reuse it. Per-run
# data goes in the user message, not here.
SYSTEM_PROMPT = """\
You are a focus coach on a small device on the user's desk. Three times a \
day you get their priorities, what they have done, and what the device has \
logged (self-reports, pauses, phone taken off its charger during focus time). \
Reply with what helps them do the right next thing.

`facts` already has the counts and totals: the next priority, phone pickups \
and minutes off the charger, what has carried over three days or more, and \
whether they have paused. Use those numbers as given; never work them out \
again from the events. The phone belongs on its charger during focus time, \
so phone_out means it was picked up.

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
- Friday evening (weekly_review is true): look back at the whole week \
instead, using this_week and today: what got done, what kept carrying over, \
how the phone and breaks went, and one thing to try next week. End the \
week on something they did well.

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


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model wrote it


@dataclass
class Response:
    text: str
    model: str
    usage: Usage
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

    @property
    def stop_reason(self) -> str | None:
        return "tool_use" if self.tool_calls else self.finish_reason

    def as_message(self) -> dict:
        """This turn as an assistant message, to send back with the tool results."""
        message: dict = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return message


def facts(s: State, now: str) -> dict:
    """Counts and totals worked out in code, so the model never has to do
    arithmetic on timestamps (open models get it wrong surprisingly often)."""
    open_ = s.open_priorities
    pickups, minutes, out_at = 0, 0.0, None
    try:
        for e in s.events:
            if e["kind"] == st.PHONE_OUT:
                pickups += 1
                out_at = datetime.fromisoformat(e["at"])
            elif e["kind"] == st.PHONE_IN and out_at is not None:
                minutes += (datetime.fromisoformat(e["at"]) - out_at).total_seconds() / 60
                out_at = None
        if out_at is not None:
            minutes += max(0.0, (datetime.fromisoformat(now) - out_at).total_seconds() / 60)
    except (KeyError, ValueError, TypeError):
        pass  # a malformed or mixed-timezone event: leave the totals as far as they got
    reports = [e.get("detail") for e in s.events if e["kind"] == st.SELF_REPORT]
    return {
        "next_up": open_[0].text if open_ else None,
        "open_count": len(open_),
        "done_count": len(s.priorities) - len(open_),
        "carried_3_days_or_more": [p.text for p in open_ if p.carried_days >= 3],
        "phone_pickups_today": pickups,
        "phone_minutes_off_charger_today": round(minutes),
        "phone_off_charger_now_since": out_at.strftime("%H:%M") if out_at else None,
        "paused": pause.describe(s, datetime.fromisoformat(now)),
        "last_self_report": reports[-1] if reports else None,
    }


def build_payload(s: State, slot: str, now: str) -> dict:
    payload = {
        "checkin": slot,
        "now": now,
        "facts": facts(s, now),
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
        # "usage" is bookkeeping for `focus status`, not something the coach needs.
        "events_today": [{k: v for k, v in e.items() if k != "usage"} for e in s.events],
        "chat_today": [{"at": c["at"], "user": c["user"], "reply": c["reply"]} for c in s.chat[-6:]],
        "recent_days": s.history[-5:],
    }
    today = date.fromisoformat(s.date)
    if slot == "evening" and today.weekday() == 4:
        # Friday: the evening check-in becomes the week's wrap-up.
        monday = (today - timedelta(days=4)).isoformat()
        payload["weekly_review"] = True
        payload["this_week"] = [h for h in s.history if h["date"] >= monday]
    return payload


def make_client(hint: str, timeout: float = 60.0, max_retries: int = 3) -> openai.OpenAI:
    base_url = os.environ.get("FOCUS_LLM_BASE_URL")
    key = os.environ.get("FOCUS_LLM_API_KEY")
    if not base_url or not key:
        raise CoachError(f"FOCUS_LLM_BASE_URL and FOCUS_LLM_API_KEY must be set; {hint}")
    return openai.OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=max_retries)


class ThinkFilter:
    """Drops <think>...</think> from text that may arrive in pieces."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while self._buf:
            tag = self.CLOSE if self._inside else self.OPEN
            i = self._buf.find(tag)
            if i >= 0:
                if not self._inside:
                    out.append(self._buf[:i])
                self._buf = self._buf[i + len(tag):]
                self._inside = not self._inside
                continue
            # Hold back what could be the start of a tag split across pieces.
            keep = next((k for k in range(min(len(tag) - 1, len(self._buf)), 0, -1)
                         if tag.startswith(self._buf[-k:])), 0)
            if not self._inside:
                out.append(self._buf[:len(self._buf) - keep])
            self._buf = self._buf[len(self._buf) - keep:]
            break
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf = ""
        return rest


def strip_thinking(text: str) -> str:
    f = ThinkFilter()
    return (f.feed(text) + f.flush()).strip()


def create(cfg: Config, client: openai.OpenAI, model: str | None = None,
           on_text: Callable[[str], None] | None = None, system: str | None = None,
           messages: list[dict] | None = None, tools: list[dict] | None = None) -> Response:
    """One chat-completions call with the error handling every caller wants.

    With `on_text`, the reply is streamed and each piece of visible text is
    handed over as it arrives (voice starts speaking before it is finished).
    """
    model = model or cfg.model
    if not model:
        raise CoachError("FOCUS_MODEL is not set; check /etc/focus-assistant/focus.env")
    request: dict = dict(
        model=model,
        max_tokens=4000,
        messages=([{"role": "system", "content": system}] if system else []) + list(messages or []),
    )
    if tools:
        request["tools"] = tools
    if cfg.effort:
        # Only reasoning models accept this; leave FOCUS_EFFORT empty for the rest.
        request["reasoning_effort"] = cfg.effort
    try:
        if on_text is None:
            return _complete(client.chat.completions.create(**request))
        return _stream(client.chat.completions.create(
            **request, stream=True, stream_options={"include_usage": True}), model, on_text)
    except openai.AuthenticationError as exc:
        raise CoachError("API key rejected; check FOCUS_LLM_API_KEY in /etc/focus-assistant/focus.env") from exc
    except openai.RateLimitError as exc:
        raise CoachError("Rate limited by the model provider, even after retries") from exc
    except openai.APIStatusError as exc:
        raise CoachError(f"Model provider error {exc.status_code}: {exc.message}") from exc
    except openai.APIConnectionError as exc:
        raise CoachError("The coach is offline: can't reach the model provider") from exc


def _complete(completion) -> Response:
    choice = completion.choices[0]
    message = choice.message
    usage = completion.usage
    return Response(
        text=strip_thinking(message.content or ""),
        model=completion.model or "",
        usage=Usage(usage.prompt_tokens, usage.completion_tokens) if usage else Usage(),
        tool_calls=[ToolCall(c.id, c.function.name, c.function.arguments or "{}")
                    for c in (message.tool_calls or [])],
        finish_reason=choice.finish_reason,
    )


def _stream(chunks, model: str, on_text: Callable[[str], None]) -> Response:
    visible = ThinkFilter()
    text: list[str] = []
    calls: dict[int, dict] = {}  # tool calls arrive in pieces, keyed by index
    usage = Usage()
    finish = None
    for chunk in chunks:
        model = chunk.model or model
        if chunk.usage:
            usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        delta = choice.delta
        if delta.content:
            piece = visible.feed(delta.content)
            if piece:
                text.append(piece)
                on_text(piece)
        for d in delta.tool_calls or []:
            call = calls.setdefault(d.index, {"id": "", "name": "", "arguments": ""})
            call["id"] = d.id or call["id"]
            if d.function:
                call["name"] += d.function.name or ""
                call["arguments"] += d.function.arguments or ""
    tail = visible.flush()
    if tail:
        text.append(tail)
        on_text(tail)
    return Response(
        text="".join(text).strip(),
        model=model,
        usage=usage,
        tool_calls=[ToolCall(c["id"], c["name"], c["arguments"] or "{}") for _, c in sorted(calls.items())],
        finish_reason=finish,
    )


def reply_text(response: Response) -> str:
    if not response.text:
        raise CoachError(f"Empty reply (finish_reason={response.finish_reason})")
    return response.text


def ask(cfg: Config, payload: dict, client: openai.OpenAI | None = None) -> CoachReply:
    client = client or make_client(
        "check-ins get them from the systemd unit (run one with: sudo systemctl start focus-checkin)"
    )
    response = create(
        cfg, client,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    return CoachReply(
        text=reply_text(response),
        # The name from focus.env, so `focus status` can match it to its price.
        model=cfg.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def offline_reply(s: State, slot: str) -> str:
    """What a check-in says when the model can't be reached: fixed, but from the data."""
    top = s.open_priorities[0].text if s.open_priorities else None
    if slot == "morning":
        if top:
            return f"The coach is offline. Start with: {top}."
        return "The coach is offline. Set up to three priorities for today."
    if slot == "midday":
        if top:
            return f"The coach is offline. Still open at the top: {top}."
        return "The coach is offline. Everything on today's list is done."
    done = len(s.priorities) - len(s.open_priorities)
    tomorrow = f"; first thing tomorrow: {top}." if top else "."
    return f"The coach is offline. {done} done today{tomorrow} Put the phone away for the night."
