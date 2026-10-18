"""The Inky layout as an HTML page, for any browser on the home network.

The panel has the same content as `outputs/inky.py`: the latest check-in's
slot and the date, the top 3 open priorities (red tag = days carried) and
the reply. Under it, a status strip the e-ink panel doesn't have: where you
are in the day (focus block, break, between blocks), the next 50/10 break,
the phone, presence, and today's counts.

The dock service serves it at /display, through `Page`. Every POLL_SECONDS
the page asks for itself again with its version as an ETag; the answer is a
bodyless 304 until the content changes, and then the new content is swapped
in place, so a chat, voice, CLI or dock change shows up within a couple of
seconds. `Page` re-renders only when a file the page reads, or the minute,
has changed, so an unchanged poll costs two stat() calls.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import date, datetime, timedelta
from html import escape

from . import blocks, pause, presence
from .config import Config
from . import state as st
from .state import PHONE_OUT, STRETCH_REMINDER, State

POLL_SECONDS = 2
# A fallback in case the script can't run: a plain reload this often.
REFRESH_SECONDS = 300

# Tones for status chips: plain, good (green dot), alert (red dot).
PLAIN, GOOD, ALERT = "plain", "good", "alert"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<noscript><meta http-equiv="refresh" content="{refresh}"></noscript>
<title>Focus</title>
<style>
  :root {{ --paper: #fff; --ink: #111; --red: #d0021b; --green: #1a7f37; --page: #e9e7e2;
           --chip: #f7f6f3; --chip-ink: #222; --muted: #666; --line: rgba(0,0,0,.08); }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --page: #1b1b1b; --chip: #2a2a2a; --chip-ink: #e8e8e8; --muted: #999;
             --line: rgba(255,255,255,.08); --green: #3fb950; }}
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--page); min-height: 100vh; display: grid; place-content: center;
         grid-template-columns: minmax(0, 640px);
         padding: 16px; font-family: "DejaVu Sans", "Segoe UI", system-ui, sans-serif; }}
  .panel {{ width: 100%; aspect-ratio: 8 / 5; background: var(--paper); color: var(--ink);
           padding: clamp(14px, 3.75%, 24px); display: flex; flex-direction: column;
           box-shadow: 0 2px 12px rgba(0,0,0,.25); container-type: inline-size; }}
  header {{ display: flex; justify-content: space-between; font-weight: 700;
            font-size: clamp(11px, 2.8cqi, 18px); }}
  .slot {{ color: var(--red); letter-spacing: .02em; }}
  /* Room for three rows always, so the reply never jumps when priorities change. */
  ol {{ list-style: none; padding: 0; margin-top: 3.5cqi; font-size: clamp(14px, 4.1cqi, 26px);
        line-height: 1.45; height: 4.35em; }}
  li {{ display: flex; align-items: baseline; gap: 2cqi; font-weight: 700; }}
  li .n {{ color: var(--red); }}
  li .t {{ flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  li .c {{ color: var(--red); font-size: .7em; }}
  li.none {{ font-weight: 700; }}
  hr {{ border: 0; border-top: 2px solid var(--ink); margin: 1.3cqi 0 2.5cqi; }}
  .reply {{ flex: 1; overflow: hidden; font-size: clamp(13px, 3.4cqi, 22px); line-height: 1.3;
            display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 6; }}
  .strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px;
           margin-top: 12px; }}
  .chip {{ background: var(--chip); color: var(--chip-ink); border: 1px solid var(--line);
          border-radius: 8px; padding: 8px 10px; min-width: 0; }}
  .chip .k {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
  .chip .v {{ font-size: 14px; font-weight: 600; margin-top: 2px; overflow-wrap: anywhere; }}
  .chip.good .v::before, .chip.alert .v::before {{ content: ""; display: inline-block; width: 8px;
      height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }}
  .chip.good .v::before {{ background: var(--green); }}
  .chip.alert .v::before {{ background: var(--red); }}
  footer {{ margin-top: 12px; font-size: 12px; color: var(--muted); text-align: center; }}
</style>
</head>
<body>
<main>
<div class="panel">
  <header><span class="slot">{slot}</span><span>{day}</span></header>
  <ol>{priorities}</ol>
  <hr>
  <p class="reply">{reply}</p>
</div>
<section class="strip">{chips}</section>
<footer>Updated {updated} · live</footer>
</main>
<script>
let version = "{version}";
async function check() {{
  if (document.hidden) return;
  try {{
    const r = await fetch(location.href, {{ cache: "no-store", headers: {{ "If-None-Match": '"' + version + '"' }} }});
    if (r.status !== 200) return;  // 304: nothing changed
    const fresh = new DOMParser().parseFromString(await r.text(), "text/html").querySelector("main");
    document.querySelector("main").replaceWith(fresh);
    version = (r.headers.get("ETag") || "").replaceAll('"', "");
  }} catch (e) {{}}
}}
setInterval(check, {poll_ms});
document.addEventListener("visibilitychange", check);
</script>
</body>
</html>
"""


def _hm(t) -> str:
    return t.strftime("%H:%M")


def where_in_day(now: datetime, focus_blocks: list[blocks.Block]) -> list[tuple[str, str, str]]:
    """The "Now" chip and, inside a block, the next 50/10 break."""
    if now.weekday() >= 5:
        return [("Now", "Weekend", PLAIN)]
    t = now.time()
    for start, end in focus_blocks:
        if start <= t < end:
            if now.minute >= 50:
                back = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).time()
                return [("Now", f"Break till {_hm(min(back, end))}", GOOD)]
            chips = [("Now", f"Focus until {_hm(end)}", ALERT)]
            brk = now.replace(minute=50, second=0, microsecond=0).time()
            chips.append(("Next break", _hm(brk) if brk < end else f"Block ends {_hm(end)}", PLAIN))
            return chips
    upcoming = [start for start, _ in focus_blocks if start > t]
    if upcoming:
        label = "Starts" if all(t < s for s, _ in focus_blocks) else "Next block"
        return [("Now", f"{label} at {_hm(min(upcoming))}", PLAIN)]
    return [("Now", "Done for the day", GOOD)]


def day_status(state: State, now: datetime, cfg: Config | None = None,
               focus_blocks: list[blocks.Block] | None = None) -> list[tuple[str, str, str]]:
    """(label, value, tone) chips for the strip. `now` must be timezone-aware."""
    focus_blocks = blocks.from_env() if focus_blocks is None else focus_blocks
    chips = where_in_day(now, focus_blocks)

    out = presence.phone_out_since(state)
    if out:
        chips.append(("Phone", f"Off since {_hm(out)}", ALERT))
    elif blocks.in_block(now, focus_blocks):
        chips.append(("Phone", "On charger", GOOD))

    quiet = pause.describe(state, now)
    if quiet:
        chips.append(("Paused", quiet, ALERT))

    if cfg is not None:
        if presence.away(cfg, state, now):
            chips.append(("You", "Away", ALERT))
        if any(c.get("unheard") for c in state.checkins):
            chips.append(("Check-in", "Waiting for you", ALERT))

    done = len(state.priorities) - len(state.open_priorities)
    pickups = sum(1 for e in state.events if e["kind"] == PHONE_OUT)
    breaks = sum(1 for e in state.events if e["kind"] == STRETCH_REMINDER)
    chips.append(("Today", f"{done} done, {len(state.open_priorities)} open", PLAIN))
    chips.append(("Phone pickups", str(pickups), ALERT if pickups >= 5 else PLAIN))
    chips.append(("Breaks", str(breaks), PLAIN))
    return chips


def _content(state: State, now: str, cfg: Config | None,
             focus_blocks: list[blocks.Block] | None) -> dict[str, str]:
    """What the page shows, without the clock in its footer."""
    last = state.checkins[-1] if state.checkins else None
    slot = f"{last['slot'].upper()} CHECK-IN" if last else "NO CHECK-IN YET"
    reply = (last.get("reply") or "") if last else "The first check-in of the day will appear here."

    rows = []
    for i, p in enumerate(state.open_priorities[:3], 1):
        carried = f'<span class="c">{p.carried_days}d</span>' if p.carried_days else ""
        rows.append(f'<li><span class="n">{i}</span><span class="t">{escape(p.text)}</span>{carried}</li>')
    if not rows:
        rows.append('<li class="none">No open priorities</li>')

    chips = "".join(
        f'<div class="chip {tone}"><div class="k">{escape(k)}</div><div class="v">{escape(v)}</div></div>'
        for k, v, tone in day_status(state, datetime.fromisoformat(now), cfg, focus_blocks)
    )
    return {
        "slot": slot,
        "day": date.fromisoformat(state.date).strftime("%a %d %b"),
        "priorities": "".join(rows),
        "reply": escape(reply),
        "chips": chips,
    }


def render(state: State, now: str, cfg: Config | None = None,
           focus_blocks: list[blocks.Block] | None = None) -> tuple[str, str]:
    """(version, html). The version changes exactly when the content does, not
    with the footer's clock. `now` is an ISO timestamp with its offset."""
    content = _content(state, now, cfg, focus_blocks)
    version = hashlib.sha256("\0".join(content.values()).encode()).hexdigest()[:16]
    return version, _PAGE.format(
        refresh=REFRESH_SECONDS,
        poll_ms=POLL_SECONDS * 1000,
        version=version,
        updated=escape(now[11:16]),
        **content,
    )


def render_html(state: State, now: str, cfg: Config | None = None,
                focus_blocks: list[blocks.Block] | None = None) -> str:
    return render(state, now, cfg, focus_blocks)[1]


class Page:
    """The rendered page, cached until something it shows could have changed:
    state.json, presence.json, or the minute (the day chips move with time,
    and the date in it also covers the midnight rollover)."""

    def __init__(self, cfg: Config, focus_blocks: list[blocks.Block]):
        self.cfg = cfg
        self.focus_blocks = focus_blocks
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._page: tuple[str, str] = ("", "")

    @staticmethod
    def _stamp(path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_mtime_ns, info.st_size

    def get(self, now: str) -> tuple[str, str]:
        """(version, html) at `now`, an ISO timestamp with its offset."""
        key = (self._stamp(self.cfg.state_path), self._stamp(presence.sound_path(self.cfg)), now[:16])
        with self._lock:
            if key != self._key:
                self._page = render(st.peek(self.cfg), now, self.cfg, self.focus_blocks)
                self._key = key
            return self._page
