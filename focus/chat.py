"""Talk to the coach between check-ins: one message in, one short reply out.

The model can change priorities only through the tools below, which call
`focus.actions` like every other input. Today's exchanges are kept in state
(cleared at rollover) so follow-ups make sense and check-ins can see them.
Only the plain text of earlier turns is replayed; each turn's tool calls
stay inside that turn.
"""

from __future__ import annotations

import json
from typing import Callable

import anthropic

from . import actions, coach, state as st
from .coach import CoachError
from .config import Config
from .state import State

MAX_STEPS = 6          # model calls per message, so a confused loop can't run up a bill
HISTORY_TURNS = 10     # earlier exchanges replayed for context
MAX_MESSAGE_CHARS = 1000

SYSTEM_PROMPT = """\
You are the user's focus coach, the same one that checks in three times a \
day from a small device on their desk. Now they are messaging you from their \
phone, often by voice. Each message comes with their current priorities, \
today's check-ins and device events.

Help them decide and keep going: answer questions about their day, and \
change their priorities when they ask, using the tools. Use the priority ids \
from the data. If a request is ambiguous, ask one short question instead of \
guessing. Never invent tasks, progress or events.

Your reply may be read aloud, so write plain sentences: no markdown, no \
lists, no emoji, at most 60 words. After changing something, say what you \
changed in a few words."""


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_ID = {"type": "integer", "description": "Priority id from the data"}

TOOLS = [
    _tool("add_priority", "Add a priority to the end of today's list.",
          {"text": {"type": "string", "description": "Short imperative, e.g. 'Email Sam the budget'"}}),
    _tool("complete_priority", "Mark a priority done.", {"id": _ID}),
    _tool("reopen_priority", "Mark a done priority as not done.", {"id": _ID}),
    _tool("drop_priority", "Remove a priority entirely.", {"id": _ID}),
    _tool("move_priority", "Move a priority to a 1-based position; 1 is the top.",
          {"id": _ID, "position": {"type": "integer", "description": "1 = top"}}),
    _tool("report", "Log how the current focus block is going, when the user says so.",
          {"how": {"type": "string", "enum": ["on_track", "drifted"]}}),
]

_HANDLERS: dict[str, Callable[[State, dict], object]] = {
    "add_priority": lambda s, a: actions.add_priority(s, a["text"]),
    "complete_priority": lambda s, a: actions.complete_priority(s, a["id"]),
    "reopen_priority": lambda s, a: actions.reopen_priority(s, a["id"]),
    "drop_priority": lambda s, a: actions.drop_priority(s, a["id"]),
    "move_priority": lambda s, a: actions.move_priority(s, a["id"], a["position"]),
    "report": lambda s, a: actions.log_event(s, st.SELF_REPORT, a["how"]),
}


def _priorities_text(s: State) -> str:
    if not s.priorities:
        return "No priorities."
    return "\n".join(
        f"{i}. id={p.id} {'[done] ' if p.done else ''}{p.text}"
        for i, p in enumerate(s.priorities, 1)
    )


def _run_tool(cfg: Config, name: str, args: dict) -> tuple[str, bool]:
    """Apply one tool call; returns (result text, is_error)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name}", True
    try:
        with st.locked_state(cfg) as s:
            handler(s, args)
            return "Done. Priorities now:\n" + _priorities_text(s), False
    except (st.StateError, KeyError, TypeError) as exc:
        return f"Error: {exc}", True


def run(cfg: Config, message: str, client: anthropic.Anthropic | None = None,
        daily_limit: int = 50) -> str:
    message = message.strip()
    if not message:
        raise CoachError("Empty message")
    message = message[:MAX_MESSAGE_CHARS]

    # Snapshot under the lock; never hold it during a model call.
    with st.locked_state(cfg) as s:
        if len(s.chat) >= daily_limit:
            raise CoachError(f"Daily chat limit ({daily_limit}) reached; it resets tomorrow")
        payload = coach.build_payload(s, "chat", st.now_iso())
        history = s.chat[-HISTORY_TURNS:]

    # Short timeouts: the phone is waiting, and a Shortcut gives up after about a minute.
    client = client or coach.make_client(
        "the chat service gets it from /etc/focus-assistant/focus.env", timeout=25.0, max_retries=1
    )
    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["reply"]})
    messages.append({
        "role": "user",
        "content": f"<data>\n{json.dumps(payload, ensure_ascii=False)}\n</data>\n\n{message}",
    })

    tokens_in = tokens_out = 0
    changed: list[str] = []
    for _ in range(MAX_STEPS):
        response = coach.create(cfg, client, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)
        tokens_in += response.usage.input_tokens
        tokens_out += response.usage.output_tokens
        if response.stop_reason != "tool_use":
            break
        # Append the whole turn unchanged (thinking blocks included), then all results at once.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            text, is_error = _run_tool(cfg, block.name, block.input)
            if not is_error:
                changed.append(block.name)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        raise CoachError("The coach took too many steps; try a simpler request")

    reply = coach.reply_text(response)
    with st.locked_state(cfg) as s:
        s.chat.append({
            "at": st.now_iso(),
            "user": message,
            "reply": reply,
            "changed": changed,
            "tokens": {"in": tokens_in, "out": tokens_out},
        })
    return reply
