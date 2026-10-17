"""`focus status`: everything worth checking during testing, in one screen.

Services and timers (from systemd), today's priorities, check-ins, focus
block, phone, desk sound and presence, and what today's API calls cost,
from the token counts recorded in state.json.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Callable

from . import blocks, presence, stretch
from .config import Config
from .state import State

UNITS = ["focus-checkin.timer", "focus-stretch.timer", "focus-dock", "focus-chat", "focus-voice"]


def prices() -> dict[str, tuple[float, float]]:
    """US$ per million input / output tokens, from focus.env. Open-model prices
    depend on the provider, so they are settings, not a table here:

        FOCUS_MODEL_PRICE=0.59,0.79        for FOCUS_MODEL
        FOCUS_CHAT_MODEL_PRICE=0.05,0.08   for FOCUS_CHAT_MODEL
    """
    table = {}
    for model_var, price_var in (("FOCUS_MODEL", "FOCUS_MODEL_PRICE"),
                                 ("FOCUS_CHAT_MODEL", "FOCUS_CHAT_MODEL_PRICE")):
        model, raw = os.environ.get(model_var), os.environ.get(price_var, "")
        try:
            tin, tout = (float(x) for x in raw.split(","))
        except ValueError:
            continue
        if model:
            table[model] = (tin, tout)
    return table


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    price = prices().get(model)
    if price is None:
        return None
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


def usage_today(cfg: Config, s: State) -> list[tuple[str, int, int, int, float | None]]:
    """(what, calls, tokens in, tokens out, dollars or None if a model's price is unknown)."""
    groups: dict[str, list[tuple[str, int, int]]] = {"check-ins": [], "chat": [], "stretch": []}
    for c in s.checkins:
        t = c.get("tokens", {})
        groups["check-ins"].append((c.get("model", cfg.model), t.get("in", 0), t.get("out", 0)))
    for c in s.chat:
        t = c.get("tokens", {})
        groups["chat"].append((c.get("model", cfg.chat_model), t.get("in", 0), t.get("out", 0)))
    for e in s.events:
        if e["kind"] == stretch.STRETCH and "usage" in e:
            u = e["usage"]
            groups["stretch"].append((u["model"], u["in"], u["out"]))

    rows = []
    for name, calls in groups.items():
        dollars = [cost(m, i, o) for m, i, o in calls]
        rows.append((name, len(calls), sum(i for _, i, _ in calls), sum(o for _, _, o in calls),
                     None if None in dollars else sum(dollars)))
    return rows


def _systemctl(*args: str) -> str | None:
    try:
        out = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip()


def service_lines(systemctl: Callable[..., str | None] = _systemctl) -> list[str]:
    lines = []
    for unit in UNITS:
        state = systemctl("is-active", unit)
        if state is None:
            return ["  (systemctl not available here)"]
        line = f"  {unit:<20} {state or 'unknown'}"
        if unit.endswith(".timer") and state == "active":
            nxt = systemctl("show", unit, "-p", "NextElapseUSecRealtime", "--value")
            if nxt:
                line += f"   next {nxt}"
        lines.append(line)
    return lines


def _hm(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M")


def report(cfg: Config, s: State, now: datetime,
           systemctl: Callable[..., str | None] = _systemctl) -> str:
    """`now` must be timezone-aware, like the timestamps in state."""
    open_ = len(s.open_priorities)
    done = len(s.priorities) - open_
    checkins = ", ".join(f"{c['slot']} {_hm(c['at'])}" + (" (held, away)" if c.get("unheard") else "")
                         for c in s.checkins) or "none yet"
    focus_blocks = blocks.from_env()
    current = next((f"{a:%H:%M}-{b:%H:%M}" for a, b in focus_blocks
                    if blocks.in_block(now, [(a, b)])), None)
    out = presence.phone_out_since(s)
    heard = presence.last_sound(cfg)
    reminders = [e for e in s.events if e["kind"] == stretch.STRETCH]
    by_coach = sum(1 for e in reminders if e.get("detail") == "coach")
    voice_chats = sum(1 for c in s.chat if c.get("voice"))

    lines = [f"Focus status, {now:%a %d %b %H:%M}", "", "Services"]
    lines += service_lines(systemctl)
    lines += [
        "",
        "Today",
        f"  Priorities   {open_} open, {done} done",
        f"  Check-ins    {checkins}",
        f"  Focus block  {'yes, ' + current if current else 'no'}",
        f"  Phone        {'off the dock since ' + out.strftime('%H:%M') if out else 'on the dock (or no dock events today)'}",
        f"  Desk sound   {'last heard ' + heard.strftime('%H:%M') if heard else 'no reading (voice service off?)'}",
        f"  Presence     {'away' if presence.away(cfg, s, now) else 'at the desk'}",
        f"  Stretch      {len(reminders)} reminders ({by_coach} by the coach, {len(reminders) - by_coach} fixed)",
        f"  Chat         {len(s.chat)} messages ({voice_chats} by voice)",
        "",
        "API cost today",
    ]
    total, unknown = 0.0, False
    for name, calls, tin, tout, dollars in usage_today(cfg, s):
        price = f"${dollars:.4f}" if dollars is not None else "$? (set FOCUS_MODEL_PRICE)"
        lines.append(f"  {name:<10} {calls:>3} calls  {tin:>7,} in / {tout:>6,} out  {price}")
        if dollars is None:
            unknown = True
        else:
            total += dollars
    lines.append(f"  {'total':<10} ${total:.4f}" + (" + unknown" if unknown else ""))
    return "\n".join(lines)
