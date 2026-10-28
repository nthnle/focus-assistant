"""The status page: the e-ink panel's layout in a browser, with a list editor.

    GET  http://<pi>.local:8765/display?k=<FOCUS_DISPLAY_TOKEN>
    POST http://<pi>.local:8765/display/list?k=<FOCUS_DISPLAY_TOKEN>
         {"op": "add", "text": "..."}
         {"op": "rename", "id": 3, "text": "..."}
         {"op": "done" | "reopen" | "drop" | "restore", "id": 3}
         {"op": "move", "id": 3, "on": "today" | "later" | "2026-10-26"}

The page (focus.display) polls with If-None-Match and gets a bodyless 304
until something changes. Its editor posts one change at a time, which goes
through focus.actions like every other input. The service answers private
(home network) addresses with the right token and nothing else, writes only
the state folder, and never holds the API key.

    python -m focus.inputs.page    the service (what focus-page runs)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler

from .. import actions, blocks as focus_blocks, config, display, state as st
from ..blocks import Block
from ..config import Config
from . import _web

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
# One change is a few dozen bytes; anything much bigger isn't from the page.
MAX_BODY = 4096


class BadRequest(ValueError):
    """A change the editor can't make; the message is shown on the page."""


def _text(change: dict) -> str:
    text = change.get("text")
    if not isinstance(text, str):
        raise BadRequest("Type something first")
    text = " ".join(text.split())  # one line, however it was pasted
    if len(text) > display.MAX_TEXT:
        raise BadRequest(f"Keep it under {display.MAX_TEXT} characters")
    return text


def _id(change: dict) -> int:
    pid = change.get("id")
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise BadRequest("Which item?")
    return pid


def _on(change: dict) -> date | str | None:
    on = change.get("on")
    if on == "today":
        return None
    if on == st.LATER:
        return st.LATER
    try:
        return date.fromisoformat(on)
    except (TypeError, ValueError):
        raise BadRequest("Which day?") from None


def edit_list(cfg: Config, change) -> str:
    """Make one change from the page's editor; returns what was done, for the log."""
    if not isinstance(change, dict):
        raise BadRequest("Not a change")
    op = change.get("op")
    with st.locked_state(cfg) as s:
        if op == "add":
            p = actions.add_priority(s, _text(change))
        elif op == "rename":
            p = actions.rename_priority(s, _id(change), _text(change))
        elif op == "done":
            p = actions.complete_priority(s, _id(change))
        elif op == "reopen":
            p = actions.reopen_priority(s, _id(change))
        elif op == "drop":
            p = actions.drop_priority(s, _id(change))
        elif op == "restore":
            p = actions.restore_priority(s, _id(change))
        elif op == "move":
            p = actions.schedule_priority(s, _id(change), _on(change))
        else:
            raise BadRequest("Not a change the page can make")
    return f"{op} {config.words(p.text)}"


def make_handler(cfg: Config, blocks: list[Block], token: str) -> type[BaseHTTPRequestHandler]:
    page = display.Page(cfg, blocks)

    class Handler(BaseHTTPRequestHandler):
        timeout = 10
        # Keep-alive: the page polls every 2 s over one connection, not a new one each time.
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if _web.path_of(self) != "/display":
                self.send_error(404)
                return
            if not _web.authorized(self, token, in_query=True):
                return
            version, html = page.get(st.now_iso())
            etag = f'"{version}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not _web.authorized(self, token, in_query=True):
                return
            length = _web.content_length(self)
            if length is None or length > MAX_BODY:
                self.close_connection = True  # the body is left unread
                self._json(413, {"error": "Too long"})
                return
            raw = self.rfile.read(length)
            if _web.path_of(self) != "/display/list":
                self.send_error(404)
                return
            # Only fetch() with a JSON body gets here: a plain form on another
            # site can't send this type without the browser asking first.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._json(415, {"error": "Send JSON"})
                return
            try:
                done = edit_list(cfg, json.loads(raw))
            except json.JSONDecodeError:
                self._json(400, {"error": "Not JSON"})
                return
            except (BadRequest, st.StateError) as exc:
                self._json(400, {"error": str(exc)})
                return
            log.info("Page: %s", done)
            self._json(200, {"ok": True})

        def _json(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            log.debug("%s " + format, self.client_address[0], *args)

    return Handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    token = _web.token_from_env("FOCUS_DISPLAY_TOKEN")
    if token is None:
        return 1
    _web.serve(make_handler(config.load(), focus_blocks.from_env(), token),
               os.environ.get("FOCUS_PAGE_HOST", "0.0.0.0"),
               int(os.environ.get("FOCUS_PAGE_PORT", DEFAULT_PORT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
