"""Talk to the coach between check-ins: one message in, one short reply out.

The model can change priorities only through the tools below, which call
`focus.actions` like every other input. Today's exchanges are kept in state
(cleared at rollover) so follow-ups make sense and check-ins can see them.
Only the plain text of earlier turns is replayed; each turn's tool calls
stay inside that turn.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Callable

import openai

from . import actions, coach, days, feedback, pause, pricing, state as st
from .coach import CoachError
from .config import Config
from .state import State

MAX_STEPS = 6          # model calls per message, so a confused loop can't run up a bill
HISTORY_TURNS = 10     # earlier exchanges replayed for context
MAX_MESSAGE_CHARS = 1000
MAX_PRIORITY_CHARS = 200

SYSTEM_PROMPT = """\
You are the user's focus coach, the same one that checks in three times a \
day from a small device on their desk. Now they are messaging you from their \
phone, often by voice. Each message comes with their to-do list (the \
`priorities` in the data), today's check-ins and device events.

Help them get through the list: answer questions about their day, and \
change the list when they ask, using the tools. Use the item ids from the \
data. If a request is ambiguous, ask one short question instead of \
guessing. To change an item's wording, rename it; drop an item only when \
they clearly ask for it to be removed. If they want quiet for a while (a \
meeting, a call, a day off), use pause; resume ends a pause early. For \
another day, add the item and then schedule it. Week goals are one to \
three outcomes for this week, set with add_goal, usually on Monday. If \
they ask how to improve their routine, suggest one or two concrete changes \
(focus block times, breaks, where the phone lives, how long the list is), \
each tied to a pattern you can point to in recent_days, and say that focus \
blocks are changed in focus.env. Never invent tasks, progress or events. \
Take counts and totals from `facts` as given, and after a change take the \
list from the tool result's "List now", not from the data sent before the \
change.

The list is not ranked: never call an item "next" or "the top one". If \
they ask what to do, suggest one item and why.

Your reply may be read aloud, so write plain sentences: no markdown, no \
lists, no emoji, at most 60 words. After changing something, say what you \
changed in a few words, naming the item, not just "done" or "all set"."""

# Added for messages spoken at the desk: there the reply is heard, and every
# extra sentence is another second or two of waiting.
VOICE_RULES = """

This message was spoken at the desk and your reply will be read aloud. \
Answer in one short sentence, at most 15 words, unless they asked a question \
that needs more. After a change, confirm it by naming the item, like this: \
Added lawn pre-emergent."""

TALK_RULES = """

This is a spoken conversation at the desk, and your reply will be read aloud. They asked to talk, so talk naturally, like a coach sitting beside them: two or three short sentences, at most 50 words. Ask one question back when it would help them think it through. After a change, confirm it by naming the item. No lists, no markdown."""


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


_ID = {"type": "integer", "description": "Item id from the data"}

TOOLS = [
    _tool("add_priority", "Add an item to today's to-do list.",
          {"text": {"type": "string", "description": "Short imperative, e.g. 'Email Sam the budget'"}}),
    _tool("rename_priority", "Change an item's wording, e.g. to fix a typo or a mis-hearing.",
          {"id": _ID, "text": {"type": "string", "description": "The new wording"}}),
    _tool("complete_priority", "Mark an item done.", {"id": _ID}),
    _tool("reopen_priority", "Mark a done item as not done.", {"id": _ID}),
    _tool("drop_priority", "Remove an item from the list entirely. Only when they ask to remove it.", {"id": _ID}),
    _tool("report", "Log how the current focus block is going, when the user says so.",
          {"how": {"type": "string", "enum": ["on_track", "drifted"]}}),
    _tool("pause", "Hold spoken check-ins and stretch reminders until a time, or skip whole days off.",
          {"until": {"type": "string", "description": "When to start again: 11:00, 2pm, 45m, 2h, "
                                                      "tomorrow, monday, or a date like 2026-10-24"}}),
    _tool("resume", "End a pause now.", {}),
    _tool("schedule_priority", "Move a priority to another day, to 'later' (no date yet), or back to 'today'.",
          {"id": _ID, "on": {"type": "string", "description": "today, later, tomorrow, a weekday like "
                                                              "wednesday, or a date like 2026-10-24"}}),
    _tool("add_goal", "Add one of this week's goals (one to three a week); on a weekend it is for next week.",
          {"text": {"type": "string", "description": "An outcome for the week, e.g. 'Ship the beta'"}}),
    _tool("complete_goal", "Mark a week goal met.", {"id": {"type": "integer", "description": "Goal id from week_goals"}}),
    _tool("drop_goal", "Remove a week goal.", {"id": {"type": "integer", "description": "Goal id from week_goals"}}),
    _tool("rate_reply", "Record feedback on the coach's previous reply (a check-in or an earlier answer) "
                        "when the user says it was wrong, unhelpful, or spot on.",
          {"value": {"type": "string", "enum": ["good", "bad"]},
           "note": {"type": "string", "description": "What was wrong or right, in their words"}}),
]


def _day(s: State, on: str) -> date | str | None:
    """A schedule_priority day: None for today, "later", or a date."""
    word = on.strip().lower()
    if word == "today":
        return None
    if word == st.LATER:
        return st.LATER
    day = days.parse_day(word, date.fromisoformat(s.date))
    if day is None:
        raise ValueError(f"on must be today, later, tomorrow, a weekday or a date, not {on!r}")
    return day

_HANDLERS: dict[str, Callable[[State, dict], object]] = {
    "add_priority": lambda s, a: actions.add_priority(s, a["text"]),
    "rename_priority": lambda s, a: actions.rename_priority(s, a["id"], a["text"]),
    "complete_priority": lambda s, a: actions.complete_priority(s, a["id"]),
    "reopen_priority": lambda s, a: actions.reopen_priority(s, a["id"]),
    "drop_priority": lambda s, a: actions.drop_priority(s, a["id"]),
    "report": lambda s, a: actions.log_event(s, st.SELF_REPORT, a["how"]),
    "pause": lambda s, a: actions.pause(s, pause.parse_until(a["until"], datetime.now().astimezone())),
    "resume": lambda s, a: actions.resume(s),
    "schedule_priority": lambda s, a: actions.schedule_priority(s, a["id"], _day(s, a["on"])),
    "add_goal": lambda s, a: actions.add_goal(s, a["text"]),
    "complete_goal": lambda s, a: actions.complete_goal(s, a["id"]),
    "drop_goal": lambda s, a: actions.drop_goal(s, a["id"]),
}


def _priorities_text(s: State) -> str:
    if not s.priorities:
        lines = ["The list is empty."]
    else:
        lines = [
            f"{i}. id={p.id} {'[done] ' if p.done else ''}{f'[on {p.on}] ' if p.on and not p.due(s.date) else ''}{p.text}"
            for i, p in enumerate(s.ordered, 1)
        ]
    goals = [f"goal id={g['id']} {'[met] ' if g.get('done_at') else ''}{g['text']}" for g in s.this_weeks_goals]
    goals += [f"goal id={g['id']} [next week] {g['text']}" for g in s.next_weeks_goals]
    return "\n".join(lines + (["Week goals:"] + goals if goals else []))


_ENDS_WITH_NEXT = re.compile(r"[\s;,.]*\bnext (is|up)\b[^.!?]*[.!?]?\s*$", re.I)


def _without_next(reply: str) -> str:
    """An earlier reply minus its closing "next is X": replayed with it, the
    model copies the habit into every answer."""
    rest = _ENDS_WITH_NEXT.sub("", reply).rstrip()
    if not rest:
        return reply
    return rest if rest[-1] in ".!?" else rest + "."


# A sentence end glued to a capital ("Marked done.Done."): gpt-oss sometimes
# writes a second message, and the host joins the two with no space.
# The lowercase before the stop keeps "U.S." intact.
_SECOND_PART = re.compile(r"(?<=[a-z0-9)'\"’”][.!?])(?=[A-Z])")


def _first_part(text: str) -> str:
    m = _SECOND_PART.search(text)
    return text[:m.start()] if m else text


class _FirstPartFilter:
    """Streams text on until a glued-on second message starts, then drops the rest."""

    def __init__(self, out: Callable[[str], None]):
        self.out = out
        self.tail = ""      # the last characters sent, for a stop split across pieces
        self.cut = False

    def feed(self, piece: str) -> None:
        if self.cut:
            return
        joined = self.tail + piece
        m = _SECOND_PART.search(joined, len(self.tail))
        if m:
            piece, self.cut = joined[len(self.tail):m.start()], True
        if piece:
            self.out(piece)
        self.tail = joined[-2:]


def _arguments(raw: str) -> dict:
    """The model's arguments, checked: open models don't always follow the schema."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments are not valid JSON ({exc})") from exc
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    for key in ("id",):
        if key in args:
            value = args[key]
            if isinstance(value, bool) or not str(value).strip().lstrip("-").isdigit():
                raise ValueError(f"{key} must be a whole number")
            args[key] = int(value)
    if "text" in args:
        if not isinstance(args["text"], str) or not args["text"].strip():
            raise ValueError("text must be a non-empty string")
        args["text"] = args["text"].strip()[:MAX_PRIORITY_CHARS]
    if "on" in args:
        if not isinstance(args["on"], str) or not args["on"].strip():
            raise ValueError("on must be a non-empty string")
        args["on"] = args["on"].strip()[:40]
    if "value" in args and args["value"] not in ("good", "bad"):
        raise ValueError("value must be good or bad")
    if "note" in args:
        if not isinstance(args["note"], str):
            raise ValueError("note must be a string")
        args["note"] = args["note"].strip()[:500]
    if "until" in args:
        if not isinstance(args["until"], str) or not args["until"].strip():
            raise ValueError("until must be a non-empty string")
        args["until"] = args["until"].strip()[:40]
    if "how" in args and args["how"] not in ("on_track", "drifted"):
        raise ValueError("how must be on_track or drifted")
    return args


def _run_tool(cfg: Config, name: str, raw_args: str) -> tuple[str, bool]:
    """Apply one tool call; returns (result text, is_error)."""
    if name == "rate_reply":
        try:
            args = _arguments(raw_args)
            rated = feedback.rate(cfg, args["value"], args.get("note", ""))
            return f"Recorded as {args['value']}: {rated['reply'][:120]}", False
        except (KeyError, TypeError, ValueError) as exc:
            return f"Error: {exc}", True
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name}", True
    try:
        args = _arguments(raw_args)
        with st.locked_state(cfg) as s:
            handler(s, args)
            quiet = pause.describe(s, datetime.now().astimezone())
            paused = f"\nPaused {quiet}" if quiet else ""
            return f"Done.{paused}\nList now:\n" + _priorities_text(s), False
    except (st.StateError, KeyError, TypeError, ValueError) as exc:
        return f"Error: {exc}", True


def run(cfg: Config, message: str, client: openai.OpenAI | None = None,
        on_text: Callable[[str], None] | None = None,
        voice: bool = False, on_done_change: Callable[[], None] | None = None,
        talk: bool = False) -> str:
    """Answer one message. `on_text` receives the reply as it streams in;
    `voice` asks for the short replies that suit being read aloud;
    `on_done_change` is called when a priority was marked done or reopened."""
    message = message.strip()
    if not message:
        raise CoachError("Empty message")
    message = message[:MAX_MESSAGE_CHARS]

    # Snapshot under the lock; never hold it during a model call.
    with st.locked_state(cfg) as s:
        payload = coach.build_payload(s, "chat", st.now_iso())
        history = s.chat[-HISTORY_TURNS:]

    # Short timeouts: the phone is waiting, and a Shortcut gives up after about a minute.
    client = client or coach.make_client(
        "the chat service gets it from /etc/focus-assistant/focus.env", timeout=25.0, max_retries=1
    )
    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": _without_next(turn["reply"])})
    messages.append({
        "role": "user",
        "content": f"<data>\n{json.dumps(payload, ensure_ascii=False)}\n</data>\n\n{message}",
    })

    tokens_in = tokens_out = 0
    changed: list[str] = []
    stream = _FirstPartFilter(on_text).feed if on_text else None
    for _ in range(MAX_STEPS):
        response = coach.create(cfg, client, model=cfg.chat_model, on_text=stream,
                                system=SYSTEM_PROMPT + (TALK_RULES if talk else VOICE_RULES if voice else ""),
                                tools=TOOLS, messages=messages)
        tokens_in += response.usage.input_tokens
        tokens_out += response.usage.output_tokens
        if response.stop_reason != "tool_use":
            break
        # Send the turn back as it was, then one tool message per call.
        messages.append(response.as_message())
        for call in response.tool_calls:
            text, is_error = _run_tool(cfg, call.name, call.arguments)
            if not is_error:
                changed.append(call.name)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
    else:
        raise CoachError("The coach took too many steps; try a simpler request")

    reply = _first_part(coach.reply_text(response))
    with st.locked_state(cfg) as s:
        s.chat.append(pricing.priced({
            "at": st.now_iso(),
            "user": message,
            "reply": reply,
            "changed": changed,
            "voice": voice,
            "model": cfg.chat_model,
            "tokens": {"in": tokens_in, "out": tokens_out},
        }, cfg.chat_model, tokens_in, tokens_out))
    feedback.record(cfg, "voice" if voice else "chat", {**payload, "message": message}, reply, cfg.chat_model)
    if on_done_change and {"complete_priority", "reopen_priority"} & set(changed):
        on_done_change()
    return reply
