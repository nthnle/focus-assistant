"""The Inky layout as an HTML page, for any browser on the home network.

Same content as `outputs/inky.py`: the latest check-in's slot and the date,
the top 3 open priorities (red tag = days carried) and the reply. It is
rendered from state on every request, so a chat, CLI or button change shows
up on the next refresh. The dock service serves it at /display.
"""

from __future__ import annotations

from datetime import date
from html import escape

from .state import State

REFRESH_SECONDS = 30

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>Focus</title>
<style>
  :root {{ --paper: #fff; --ink: #111; --red: #d0021b; --muted: #666; --page: #e9e7e2; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --page: #1b1b1b; }} }}
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
  footer {{ margin-top: 12px; font-size: 12px; color: var(--muted); text-align: center; }}
  @media (prefers-color-scheme: dark) {{ footer {{ color: #999; }} }}
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
<footer>Updated {updated} · refreshes every {refresh} s</footer>
</main>
</body>
</html>
"""


def render_html(state: State, now: str) -> str:
    last = state.checkins[-1] if state.checkins else None
    slot = f"{last['slot'].upper()} CHECK-IN" if last else "NO CHECK-IN YET"
    reply = (last.get("reply") or "") if last else "The first check-in of the day will appear here."

    rows = []
    for i, p in enumerate(state.open_priorities[:3], 1):
        carried = f'<span class="c">{p.carried_days}d</span>' if p.carried_days else ""
        rows.append(f'<li><span class="n">{i}</span><span class="t">{escape(p.text)}</span>{carried}</li>')
    if not rows:
        rows.append('<li class="none">No open priorities</li>')

    return _PAGE.format(
        refresh=REFRESH_SECONDS,
        slot=slot,
        day=date.fromisoformat(state.date).strftime("%a %d %b"),
        priorities="".join(rows),
        reply=escape(reply),
        updated=escape(now[11:16]),
    )
