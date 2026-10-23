"""`focus status`: everything worth checking during testing, in one screen.

Services and timers (from systemd), today's priorities, check-ins, focus
block, phone, camera and presence, and what today's API calls cost,
from the token counts recorded in state.json.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Callable

from . import blocks, feedback, pause, presence, stretch
from . import state as st
from .pricing import cost, prices  # noqa: F401  (tests use these here)
from .config import Config
from .state import State

UNITS = ["focus-checkin.timer", "focus-stretch.timer", "focus-page", "focus-chat", "focus-voice",
         "focus-camera", "focus-display"]


def usage_today(cfg: Config, s: State) -> list[tuple[str, int, int, int, float | None]]:
    """(what, calls, tokens in, tokens out, dollars or None if a model's price is unknown)."""
    # (model, tokens in, tokens out, dollars saved at call time if any)
    groups: dict[str, list[tuple[str, int, int, float | None]]] = {
        "check-ins": [], "chat": [], "stretch": [], "nudges": []}
    for c in s.checkins:
        t = c.get("tokens")
        if t is not None:  # an offline check-in made no call
            groups["check-ins"].append((c.get("model", cfg.model), t.get("in", 0), t.get("out", 0), c.get("usd")))
        for r in c.get("rewrites", []):  # the check-in rewritten after the list changed
            groups["check-ins"].append((r["model"], r["tokens"]["in"], r["tokens"]["out"], r.get("usd")))
    for c in s.chat:
        t = c.get("tokens", {})
        groups["chat"].append((c.get("model", cfg.chat_model), t.get("in", 0), t.get("out", 0), c.get("usd")))
    by_kind = {stretch.STRETCH: "stretch", st.PHONE_NUDGE: "nudges"}
    for e in s.events:
        if (group := by_kind.get(e["kind"])) and "usage" in e:
            u = e["usage"]
            groups[group].append((u["model"], u["in"], u["out"], u.get("usd")))

    rows = []
    for name, calls in groups.items():
        # Saved at call time, where the prices are visible; else from this process's env.
        dollars = [usd if usd is not None else cost(m, i, o) for m, i, o, usd in calls]
        rows.append((name, len(calls), sum(c[1] for c in calls), sum(c[2] for c in calls),
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


def camera_line(cfg: Config, now: datetime) -> str:
    seen = presence.latest(cfg, now)
    if seen is None:
        return "not running (sudo systemctl status focus-camera)"
    if not seen.looking:
        return "off (it runs around focus blocks)"
    return f"{seen.label} since {seen.since:%H:%M}"


def phone_line(cfg: Config, in_use: datetime | None, now: datetime) -> str:
    if in_use:
        return f"in use since {in_use:%H:%M}"
    return "not in use" if presence.camera_seen(cfg, now) else "not watched (the camera is off)"


def _hm(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M")


def report(cfg: Config, s: State, now: datetime,
           systemctl: Callable[..., str | None] = _systemctl) -> str:
    """`now` must be timezone-aware, like the timestamps in state."""
    open_ = len(s.open_priorities)
    done = len([p for p in s.today_list if p.done])
    checkins = ", ".join(f"{c['slot']} {_hm(c['at'])}" + (" (held, away)" if c.get("unheard") else "")
                         for c in s.checkins) or "none yet"
    focus_blocks = blocks.from_env()
    current = next((f"{a:%H:%M}-{b:%H:%M}" for a, b in focus_blocks
                    if blocks.in_block(now, [(a, b)])), None)
    in_use = presence.phone_in_use_since(cfg, s, now)
    nudges = sum(1 for e in s.events if e["kind"] == st.PHONE_NUDGE)
    reminders = [e for e in s.events if e["kind"] == stretch.STRETCH]
    by_coach = sum(1 for e in reminders if e.get("detail") == "coach")
    voice_chats = sum(1 for c in s.chat if c.get("voice"))

    lines = [f"Focus status, {now:%a %d %b %H:%M}", "", "Services"]
    lines += service_lines(systemctl)
    lines += [
        "",
        "Today",
        f"  Priorities   {open_} open, {done} done" + (f", {len(s.later)} later" if s.later else ""),
        f"  Check-ins    {checkins}",
        f"  Focus block  {'yes, ' + current if current else 'no'}",
        f"  Phone        {phone_line(cfg, in_use, now)}"
        + (f", {nudges} nudge{'s' if nudges != 1 else ''}" if nudges else ""),
        f"  Camera       {camera_line(cfg, now)}",
        f"  Presence     {'away' if presence.away(cfg, now) else 'at the desk'}",
        f"  Paused       {pause.describe(s, now) or 'no'}",
        f"  Stretch      {len(reminders)} reminders ({by_coach} by the coach, {len(reminders) - by_coach} fixed)",
        f"  Chat         {len(s.chat)} messages ({voice_chats} by voice)",
        f"  Replies      {feedback.summary(cfg)} (focus feedback)",
        "",
        "API cost today",
    ]
    total, unknown = 0.0, False
    for name, calls, tin, tout, dollars in usage_today(cfg, s):
        price = f"${dollars:.4f}" if dollars is not None else "$? (no FOCUS_MODEL_PRICE when it ran)"
        lines.append(f"  {name:<10} {calls:>3} calls  {tin:>7,} in / {tout:>6,} out  {price}")
        if dollars is None:
            unknown = True
        else:
            total += dollars
    lines.append(f"  {'total':<10} ${total:.4f}" + (" + unknown" if unknown else ""))
    return "\n".join(lines)
