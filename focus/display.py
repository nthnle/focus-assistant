"""The Inky layout as an HTML page, for any browser on the home network.

The panel has the same content as `outputs/inky.py`: the latest check-in's
slot and the date, today's open items (up to SHOWN, red tag = days carried)
and the reply. Under it, a status strip the e-ink panel doesn't have: where you
are in the day (focus block, break, between blocks), the next 50/10 break,
the phone, presence, and today's counts. Under that, the list editor: add,
mark done, reword, move (to any later day) or remove items (POST /display/list, handled by
`inputs/page.py`), for when voice mishears.

The focus-page service serves it at /display, through `Page`. Every
POLL_SECONDS the page asks for itself again with its version as an ETag;
the answer is a bodyless 304 until the content changes, and then the new
content is swapped in place, so a chat, voice, CLI or camera change shows
up within a couple of seconds. The swap waits while you are typing in the
editor, so it never throws away what you typed. `Page` re-renders only when a file the page reads, or the minute,
has changed, so an unchanged poll costs two stat() calls.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import date, datetime, timedelta
from html import escape

from . import blocks, days, layout, pause, presence
from .config import Config
from . import state as st
from .state import PHONE_OUT, STRETCH_REMINDER, State

POLL_SECONDS = 2
# Open items the screen lists before "+N more"; the 4" panel fits six.
SHOWN = 6
# The longest item the editor takes, in characters.
MAX_TEXT = 200
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
         grid-template-columns: minmax(0, 600px);
         padding: 16px; font-family: "DejaVu Sans", "Segoe UI", system-ui, sans-serif; }}
  /* The upper panel is the e-ink panel: the same layout numbers (focus/layout.py)
     in panel pixels, --u, scaled to the page's width. */
  .panel {{ width: 100%; aspect-ratio: {panel_w} / {panel_h}; background: var(--paper); color: var(--ink);
           box-shadow: 0 2px 12px rgba(0,0,0,.25); container-type: inline-size; }}
  .face {{ --u: calc(100cqi / {panel_w}); height: 100%; padding: calc(var(--u) * {margin});
          display: flex; flex-direction: column; }}
  header {{ flex: none; display: flex; justify-content: space-between; font-weight: 700; line-height: 1;
            font-size: calc(var(--u) * {header_size}); height: calc(var(--u) * {header_height}); }}
  .slot {{ color: var(--red); }}
  ul {{ flex: none; list-style: none; padding: 0; font-weight: 700;
        font-size: calc(var(--u) * var(--is)); line-height: calc(var(--u) * var(--il)); }}
  li {{ display: flex; align-items: center; }}
  li .d {{ flex: none; width: calc(var(--u) * var(--dot)); height: calc(var(--u) * var(--dot));
          border-radius: 50%; background: var(--red); margin-right: calc(var(--u) * var(--is) * .6); }}
  li .t {{ flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  li .c {{ flex: none; color: var(--red); font-size: calc(var(--u) * {tag_size}); margin-left: calc(var(--u) * 12); }}
  li.more {{ font-weight: 400; color: var(--red); padding-left: calc(var(--u) * (var(--dot) + var(--is) * .6)); }}
  hr {{ flex: none; border: 0; border-top: calc(var(--u) * {rule}) solid var(--ink);
        margin: calc(var(--u) * {rule_above}) 0 calc(var(--u) * {rule_below}); }}
  /* Starts at the largest size the panel allows and shrinks to fit, like the panel. */
  .reply {{ flex: 1; min-height: 0; overflow: hidden; line-height: {reply_line};
            font-size: calc(var(--u) * var(--rs, {reply_min})); }}
  .since {{ flex: none; height: calc(var(--u) * {note_line}); padding-top: calc(var(--u) * 6); color: var(--red);
            font-weight: 700; font-size: calc(var(--u) * {note_size}); line-height: 1;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .since:empty {{ display: none; }}
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
  .edit {{ margin-top: 12px; background: var(--chip); color: var(--chip-ink); border: 1px solid var(--line);
          border-radius: 8px; padding: 10px; font-size: 15px; }}
  .edit h2 {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em;
             font-weight: 400; margin: 12px 0 4px; }}
  .edit h2:first-child {{ margin-top: 0; }}
  .edit form {{ display: flex; gap: 8px; margin-bottom: 4px; }}
  .edit input {{ flex: 1; min-width: 0; font: inherit; color: inherit; background: var(--page);
                border: 1px solid var(--line); border-radius: 6px; padding: 8px; }}
  .edit button, .edit select {{ font: inherit; font-size: 13px; color: inherit; background: var(--page);
                               border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px;
                               cursor: pointer; flex: none; }}
  .edit ul {{ list-style: none; padding: 0; font-weight: 400; font-size: 15px; line-height: 1.35; }}
  .edit select {{ max-width: 92px; }}
  .edit .x {{ font-size: 18px; line-height: 1; }}
  .edit li {{ display: flex; align-items: center; gap: 6px; padding: 4px 0; border-top: 1px solid var(--line); }}
  .edit li:first-child {{ border-top: 0; }}
  .edit .t {{ flex: 1; min-width: 0; overflow-wrap: anywhere; padding: 4px 0; white-space: normal; }}
  .edit span.t[data-op] {{ cursor: text; }}
  .edit .tick {{ width: 30px; height: 30px; padding: 0; border-radius: 50%; }}
  .edit .tick.on {{ background: var(--green); border-color: var(--green); color: #fff; }}
  .edit li.done .t {{ text-decoration: line-through; color: var(--muted); }}
  .edit input[type=date] {{ flex: none; width: 150px; padding: 5px 6px; }}
  .edit .when {{ flex: none; font-size: 12px; color: var(--muted); }}
  .edit .x {{ width: 30px; padding: 6px 0; }}
  .edit .none {{ color: var(--muted); }}
  .edit .err {{ color: var(--red); font-size: 13px; padding: 4px 0; }}
  .edit .err:empty {{ display: none; }}
</style>
</head>
<body>
<main>
<div class="panel"><div class="face" style="--is: {item_size}; --il: {item_line}; --dot: {dot}">
  <header><span class="slot">{slot}</span><span>{day}</span></header>
  <ul>{priorities}</ul>
  <hr>
  <p class="reply" data-max="{reply_max}">{reply}</p>
  <p class="since">{since}</p>
</div></div>
<section class="strip">{chips}</section>
<section class="edit" aria-label="Edit the list">{editor}</section>
<footer>Updated {updated} · live</footer>
</main>
<script>
function fit() {{
  const r = document.querySelector(".reply");
  if (!r) return;
  for (let s = +r.dataset.max; s >= {reply_min}; s--) {{
    r.style.setProperty("--rs", s);
    if (r.scrollHeight <= r.clientHeight + 1) return;
  }}
}}
fit();
addEventListener("resize", fit);
let version = "{version}";
// Typing in the editor (or rewording an item): swapping now would lose it.
function typing() {{
  return [...document.querySelectorAll(".edit input")].some(
    i => i.value.trim() || i === document.activeElement);
}}
async function check(force) {{
  if (!force && (document.hidden || typing())) return;
  try {{
    const r = await fetch(location.href, {{ cache: "no-store", headers: {{ "If-None-Match": '"' + version + '"' }} }});
    if (r.status !== 200) return;  // 304: nothing changed
    const doc = new DOMParser().parseFromString(await r.text(), "text/html");
    // New styles (after an update): swapping <main> alone would keep the old ones.
    if (doc.querySelector("style").textContent !== document.querySelector("style").textContent) {{
      location.reload();
      return;
    }}
    document.querySelector("main").replaceWith(doc.querySelector("main"));
    fit();
    version = (r.headers.get("ETag") || "").replaceAll('"', "");
  }} catch (e) {{}}
}}
setInterval(check, {poll_ms});
document.addEventListener("visibilitychange", () => check());

// The list editor: each change is one POST, then the page shows the result.
async function send(change) {{
  const err = document.querySelector(".edit .err");
  try {{
    const r = await fetch("/display/list" + location.search, {{
      method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(change) }});
    if (!r.ok) {{
      let message = "That didn't save.";
      try {{ message = (await r.json()).error || message; }} catch (e) {{}}
      if (err) err.textContent = message;
      return false;
    }}
    await check(true);
    return true;
  }} catch (e) {{
    if (err) err.textContent = "Can't reach the Pi.";
    return false;
  }}
}}
function idOf(el) {{ return +el.closest("li").dataset.id; }}
document.addEventListener("submit", async e => {{
  if (!e.target.matches(".edit form")) return;
  e.preventDefault();
  const input = e.target.querySelector("input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (!await send({{ op: "add", text }})) input.value = text;
}});
document.addEventListener("click", e => {{
  const el = e.target.closest(".edit [data-op]");
  if (!el || el.tagName === "SELECT") return;
  const op = el.dataset.op, id = idOf(el);
  if (op === "rename") return reword(el, id);
  if (op === "drop" && !confirm("Remove " + el.closest("li").querySelector(".t").textContent + "?")) return;
  send({{ op, id }});
}});
document.addEventListener("change", e => {{
  const el = e.target.closest(".edit select[data-op]");
  if (!el || !el.value) return;
  if (el.value === "pick") return pickDay(el, idOf(el));
  send({{ op: "move", id: idOf(el), on: el.value }});
}});
// "Pick a day": the browser's own date picker in place of the menu. A day
// from tomorrow on saves; Escape, or leaving it empty, puts the menu back.
function pickDay(select, id) {{
  const input = document.createElement("input");
  input.type = "date";
  input.min = select.dataset.min;
  input.setAttribute("aria-label", "Pick a day");
  select.value = "";
  select.replaceWith(input);
  input.focus();
  try {{ input.showPicker(); }} catch (e) {{}}
  // While a year is still being typed it reads as 0002, 0020...: too early to save.
  const ok = () => input.value && input.value >= input.min;
  let finished = false;
  const finish = async save => {{
    if (finished) return;
    finished = true;
    if (save && ok()) {{
      input.disabled = true;
      if (await send({{ op: "move", id, on: input.value }})) return;
    }}
    input.replaceWith(select);
  }};
  input.addEventListener("change", () => {{ if (ok()) finish(true); }});
  input.addEventListener("keydown", e => {{
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  }});
  input.addEventListener("blur", () => finish(true));
}}
// Tap an item's text to reword it: Enter or tapping away saves, Escape cancels.
function reword(span, id) {{
  const input = document.createElement("input");
  input.value = span.textContent;
  input.maxLength = {max_text};
  input.enterKeyHint = "done";
  span.replaceWith(input);
  input.focus();
  let finished = false;
  const finish = async save => {{
    if (finished) return;
    finished = true;
    const text = input.value.trim();
    if (save && text && text !== span.textContent) {{
      input.disabled = true;
      if (await send({{ op: "rename", id, text }})) return;
    }}
    input.replaceWith(span);
  }};
  input.addEventListener("keydown", e => {{
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  }});
  input.addEventListener("blur", () => finish(true));
}}
</script>
</body>
</html>
"""


# The panel's fixed numbers, for the page's styles.
_LAYOUT = dict(
    panel_w=layout.SIZE[0], panel_h=layout.SIZE[1], margin=layout.MARGIN,
    header_size=layout.HEADER_SIZE, header_height=layout.HEADER_HEIGHT, tag_size=layout.TAG_SIZE,
    rule=layout.RULE, rule_above=layout.RULE_ABOVE, rule_below=layout.RULE_BELOW,
    reply_line=layout.REPLY_LINE, reply_min=layout.REPLY_MIN_SIZE,
    note_size=layout.NOTE_SIZE, note_line=layout.NOTE_LINE,
)


def _hm(t) -> str:
    return t.strftime("%H:%M")


def where_in_day(now: datetime, focus_blocks: list[blocks.Block]) -> list[tuple[str, str, str]]:
    """The "Now" chip and, inside a block, the next 50/10 break."""
    if not days.is_workday(now.date()):
        return [("Now", "Day off", PLAIN)]
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

    if cfg is not None and presence.camera_seen(cfg, now) is not None:
        in_use = presence.phone_in_use_since(cfg, state, now)
        chips.append(("Phone", f"In use since {_hm(in_use)}", ALERT) if in_use
                     else ("Phone", "Not in use", GOOD))

    quiet = pause.describe(state, now)
    if quiet:
        chips.append(("Paused", quiet, ALERT))

    if cfg is not None:
        if presence.away(cfg, now):
            chips.append(("You", "Away", ALERT))
        if any(c.get("unheard") for c in state.checkins):
            chips.append(("Check-in", "Waiting for you", ALERT))

    done = len([p for p in state.today_list if p.done])
    pickups = sum(1 for e in state.events if e["kind"] == PHONE_OUT)
    breaks = sum(1 for e in state.events if e["kind"] == STRETCH_REMINDER)
    chips.append(("Today", f"{done} done, {len(state.open_priorities)} open", PLAIN))
    chips.append(("Phone pickups", str(pickups), ALERT if pickups >= 5 else PLAIN))
    chips.append(("Breaks", str(breaks), PLAIN))
    return chips


def _on(p: st.Priority) -> str:
    return "someday" if p.on == st.LATER else f"{date.fromisoformat(p.on):%a %d %b}"


def editor(state: State) -> str:
    """The list editor: today's open items, the ones done today, then the
    later ones. Rows carry the priorities' own ids, so a tap always changes
    the item it shows, even if the list changed meanwhile."""
    today = date.fromisoformat(state.date)
    nxt = days.next_workday(today)

    def move(p: st.Priority) -> str:
        """The Move menu, without the place the item already is."""
        options = [("", "Move")]
        if not p.due(state.date):
            options.append(("today", "To today"))
        if p.on != nxt.isoformat():
            options.append((nxt.isoformat(), f"To {nxt:%a %d %b}"))
        if p.on != st.LATER:
            options.append((st.LATER, "To later"))
        options.append(("pick", "Pick a day…"))
        return (f'<select data-op="move" data-min="{today + timedelta(days=1)}" aria-label="Move">'
                + "".join(f'<option value="{v}">{label}</option>' for v, label in options) + "</select>")

    remove = '<button class="x" data-op="drop" aria-label="Remove" title="Remove">&times;</button>'

    def text(p: st.Priority, editable: bool = True) -> str:
        tap = ' data-op="rename" title="Tap to reword"' if editable else ""
        return f'<span class="t"{tap}>{escape(p.text)}</span>'

    open_rows = "".join(
        f'<li data-id="{p.id}"><button class="tick" data-op="done" aria-label="Mark done" '
        f'title="Mark done"></button>{text(p)}{move(p)}{remove}</li>' for p in state.open_priorities)
    parts = ["<h2>Today</h2>",
             f'<form><input name="text" maxlength="{MAX_TEXT}" placeholder="Add to today" '
             'autocomplete="off" enterkeyhint="done" aria-label="Add to today"><button>Add</button></form>',
             '<p class="err" role="alert"></p>',
             f'<ul>{open_rows or "<li class=none>Nothing open</li>"}</ul>']
    done = [p for p in state.today_list if p.done]
    if done:
        parts.append("<h2>Done today</h2><ul>" + "".join(
            f'<li class="done" data-id="{p.id}"><button class="tick on" data-op="reopen" '
            f'aria-label="Not done yet" title="Not done yet">&#10003;</button>{text(p, False)}</li>'
            for p in done) + "</ul>")
    if state.later:
        parts.append("<h2>Later</h2><ul>" + "".join(
            f'<li data-id="{p.id}">{text(p)}<span class="when">{_on(p)}</span>'
            f'{move(p)}{remove}</li>'
            for p in state.later) + "</ul>")
    return "".join(parts)


def shown(state: State) -> tuple[list, int]:
    """The open items a screen lists, in the order they were added, and how
    many more there are. Over SHOWN, the last row becomes "+N more"."""
    open_ = state.open_priorities
    if len(open_) <= SHOWN:
        return open_, 0
    return open_[:SHOWN - 1], len(open_) - (SHOWN - 1)


def since_note(state: State) -> str:
    """What was marked done after the latest check-in was written, so the
    reply never reads as out of date ("Since 16:20: done Mow the lawn").
    Empty once the coach has rewritten the check-in to include it."""
    if not state.checkins:
        return ""
    last = state.checkins[-1]
    written = datetime.fromisoformat(last.get("updated_at") or last["at"])
    done = [p.text for p in state.today_list
            if p.done_at and datetime.fromisoformat(p.done_at) > written]
    return f"Since {written:%H:%M}: done {'; '.join(done)}" if done else ""


def panel_note(state: State, now: datetime) -> str:
    """The panel's bottom line: a pause (the C button), then since_note.
    `now` must be timezone-aware."""
    quiet = pause.describe(state, now)
    return " · ".join(n for n in (f"Paused {quiet}" if quiet else "", since_note(state)) if n)


def _content(state: State, now: str, cfg: Config | None,
             focus_blocks: list[blocks.Block] | None) -> dict[str, str]:
    """What the page shows, without the clock in its footer."""
    last = state.checkins[-1] if state.checkins else None
    slot = f"{last['slot'].upper()} CHECK-IN" if last else "NO CHECK-IN YET"
    reply = (last.get("reply") or "") if last else "The first check-in of the day will appear here."

    rows = []
    items, more = shown(state)
    size, line = layout.item_size(max(1, len(items) + (1 if more else 0)))
    for p in items:
        carried = f'<span class="c">{p.carried_days}d</span>' if p.carried_days else ""
        rows.append(f'<li><span class="d"></span><span class="t">{escape(p.text)}</span>{carried}</li>')
    if more:
        rows.append(f'<li class="more">+{more} more</li>')
    if not rows:
        rows.append('<li class="none">Nothing open on the list</li>')

    chips = "".join(
        f'<div class="chip {tone}"><div class="k">{escape(k)}</div><div class="v">{escape(v)}</div></div>'
        for k, v, tone in day_status(state, datetime.fromisoformat(now), cfg, focus_blocks)
    )
    return {
        "slot": slot,
        "day": date.fromisoformat(state.date).strftime("%a %d %b"),
        "priorities": "".join(rows),
        "reply": escape(reply),
        "since": escape(panel_note(state, datetime.fromisoformat(now))),
        "chips": chips,
        "editor": editor(state),
        "item_size": str(size),
        "item_line": str(line),
        "dot": str(layout.dot_size(size)),
        "reply_max": str(min(layout.REPLY_MAX_SIZE, size)),
    }


def render(state: State, now: str, cfg: Config | None = None,
           focus_blocks: list[blocks.Block] | None = None) -> tuple[str, str]:
    """(version, html). The version changes exactly when the content or the
    page itself (after an update) does, not with the footer's clock. `now`
    is an ISO timestamp with its offset."""
    content = _content(state, now, cfg, focus_blocks)
    version = hashlib.sha256("\0".join([_PAGE, *content.values()]).encode()).hexdigest()[:16]
    return version, _PAGE.format(
        refresh=REFRESH_SECONDS,
        poll_ms=POLL_SECONDS * 1000,
        max_text=MAX_TEXT,
        **_LAYOUT,
        version=version,
        updated=escape(now[11:16]),
        **content,
    )


def render_html(state: State, now: str, cfg: Config | None = None,
                focus_blocks: list[blocks.Block] | None = None) -> str:
    return render(state, now, cfg, focus_blocks)[1]


class Page:
    """The rendered page, cached until something it shows could have changed:
    state.json, camera.json, or the minute (the day chips move with time,
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
        key = (self._stamp(self.cfg.state_path), self._stamp(presence.camera_path(self.cfg)), now[:16])
        with self._lock:
            if key != self._key:
                self._page = render(st.peek(self.cfg), now, self.cfg, self.focus_blocks)
                self._key = key
            return self._page
