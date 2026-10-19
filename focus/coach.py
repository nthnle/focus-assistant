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
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

import openai

from . import blocks, pause, state as st
from .days import monday_of
from .config import Config
from .state import State

# Kept byte-stable so providers that cache prompts can reuse it. Per-run
# data goes in the user message, not here.
SYSTEM_PROMPT = """\
You are a focus coach on a small device on the user's desk. Three times a \
day you get their to-do list, what they have done, and what the device has \
logged (self-reports, pauses, phone taken off its charger during focus time). \
Reply with what helps them get through it.

`facts` already has the counts and totals: open and done items, phone pickups \
and minutes off the charger, what has carried over three days or more, and \
whether they have paused. Use those numbers as given; never work them out \
again from the events. The phone belongs on its charger during focus time, \
so phone_out means it was picked up.

week_goals are the one to three goals they set for this week. When an \
item clearly serves one, you may say so in a few words. next_week_goals, \
if present, are already set for the week ahead. `later` holds \
items scheduled for another day: leave them alone unless asked.

The reply may be shown on a small e-ink screen or spoken aloud, so write \
plain sentences: no markdown, no lists, no emoji, at most 70 words (100 for \
a weekly review).

By check-in:
- first morning of the week (week_start is true): open the week. If \
last_week.review suggested something to try, recall it in a few words. If \
week_goals is empty, ask them to set one to three goals for the week. Then \
carry on as a normal morning.
- morning: suggest one item to start with and why. The list is not ranked, \
so pick by what is due, what unblocks other items, what serves a week goal, \
or a quick win to get going. If something has carried over for three or \
more days, say so and suggest shrinking, rescheduling or dropping it.
- midday: compare progress against the morning plan. If they drifted or the \
phone came out often, name it plainly once, without guilt, and give one \
concrete reset.
- evening: acknowledge what got done, name what carries over, and suggest \
the first thing for tomorrow. Encourage putting the phone away for the night.
- Friday evening (weekly_review is true): look back at the whole week \
instead, using this_week and today: which week_goals were met, what got \
done, what kept carrying over, how the phone and breaks went. Then suggest \
one concrete change to their routine, grounded in a pattern you can point \
to in this_week (pickups bunching at one hour, things getting done late in \
the day, check-ins missed): a different focus block, a break at another \
time, the phone elsewhere during focus time, or a shorter list. `routine` \
has the current settings. End the week on something they did well.
- a weekly_review at a morning check-in (late is true) means Friday's \
wrap-up was missed: look back at last week in two or three sentences, \
suggest the routine change, then suggest one item to start with.
- rewrite_of is present: this check-in already ran at \
rewrite_of.written_at, and since then the list changed (done_since was \
marked done). Write it again for the list as it is now, at the same \
length, acknowledging what got done. Don't say it is an update.

facts.weekday says what day it is. Check-ins only run by themselves on \
weekdays; on a Saturday or Sunday they asked for one, so keep it to two \
sentences, don't judge what got done that day, and look ahead to Monday.

If the list is empty, ask what they need to get done, in one sentence. \
Never call an item "next" or rank the list.
Be warm and direct. Do not invent tasks, progress or events that are not in \
the data.

After the reply, on a line of its own, write `start: <id>` with the id of \
the item you suggested starting with, or `start: none`. The device removes \
that line; its done button marks that item."""


@dataclass
class CoachReply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    start_id: int | None = None   # the item it suggested starting with


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
    phone = st.phone_summary(s.events, now)
    reports = [e.get("detail") for e in s.events if e["kind"] == st.SELF_REPORT]
    return {
        "open_count": len(open_),
        "done_count": len([p for p in s.today_list if p.done]),
        "carried_3_days_or_more": [p.text for p in open_ if p.carried_days >= 3],
        "phone_pickups_today": phone["pickups"],
        "phone_minutes_off_charger_today": phone["minutes"],
        "phone_off_charger_now_since": phone["out_since"].strftime("%H:%M") if phone["out_since"] else None,
        "last_self_report": reports[-1] if reports else None,
        "paused": pause.describe(s, datetime.fromisoformat(now)),
        "weekday": f"{datetime.fromisoformat(now):%A}",
    }


def _goals(goals: list[dict]) -> list[dict]:
    return [{"id": g["id"], "text": g["text"], "done": bool(g.get("done_at"))} for g in goals]


def routine() -> dict:
    """The settings a routine suggestion would change."""
    return {
        "focus_blocks": [f"{a:%H:%M}-{b:%H:%M}" for a, b in blocks.from_env()],
        "stretch_reminders": "at :50 past each hour of a focus block",
        "voice_quiet_hours": "00:00-08:00",
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
            for p in s.today_list
        ],
        "later": [{"id": p.id, "text": p.text, "on": p.on} for p in s.later],
        "week_goals": _goals(s.this_weeks_goals),
        "earlier_checkins_today": [
            {"slot": c["slot"], "at": c["at"], "reply": c.get("reply")} for c in s.checkins
        ],
        # "usage" is bookkeeping for `focus status`, not something the coach needs.
        "events_today": [{k: v for k, v in e.items() if k != "usage"} for e in s.events],
        "chat_today": [{"at": c["at"], "user": c["user"], "reply": c["reply"]} for c in s.chat[-6:]],
        "recent_days": s.history[-(7 if slot == "chat" else 5):],
    }
    today = date.fromisoformat(s.date)
    monday = monday_of(today)
    if s.next_weeks_goals:
        payload["next_week_goals"] = _goals(s.next_weeks_goals)
    if slot == "chat":
        payload["routine"] = routine()
    if slot == "evening" and today.weekday() == 4:
        # Friday: the evening check-in becomes the week's wrap-up.
        payload["weekly_review"] = True
        payload["this_week"] = [h for h in s.history if h["date"] >= monday.isoformat()]
        payload["routine"] = routine()
    if slot == "morning" and s.last_week and not s.last_week.get("opened"):
        # The first morning of a new week.
        last = s.last_week
        payload["week_start"] = True
        payload["last_week"] = {"goals": _goals(last.get("goals", [])), "review": last.get("review")}
        days = [h for h in s.history if last["week"] <= h["date"] < monday.isoformat()]
        if last.get("review") is None and any(h.get("checkins") for h in days):
            # Friday's wrap-up didn't happen (a day off, the Pi was off): do it now, briefly.
            payload["weekly_review"] = True
            payload["late"] = True
            payload["this_week"] = days
            payload["routine"] = routine()
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
    text, start_id = split_start(reply_text(response))
    return CoachReply(
        text=text,
        # The name from focus.env, so `focus status` can match it to its price.
        model=cfg.model,
        start_id=start_id,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


# "start: 12" on the reply's last line; models sometimes add backticks or bold.
_START = re.compile(r"(?:^|\n)[ \t`*]*start:\s*`?(\d+|none)`?[ \t`*.]*$", re.I)


def split_start(text: str) -> tuple[str, int | None]:
    """The reply without its closing `start: <id>` line, and that id."""
    m = _START.search(text.rstrip())
    if not m:
        return text, None
    reply = text.rstrip()[:m.start()].rstrip()
    return (reply or text), (int(m.group(1)) if m.group(1).isdigit() else None)


def _items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def offline_reply(s: State, slot: str) -> str:
    """What a check-in says when the model can't be reached: fixed, but from the data."""
    open_ = len(s.open_priorities)
    if slot in ("morning", "midday"):
        if open_:
            return f"The coach is offline. {_items(open_)} open on today's list."
        if slot == "morning":
            return "The coach is offline. Add what you need to get done today."
        return "The coach is offline. Everything on today's list is done."
    done = len([p for p in s.today_list if p.done])
    left = f", {open_} still open." if open_ else "."
    return f"The coach is offline. {done} done today{left} Put the phone away for the night."
