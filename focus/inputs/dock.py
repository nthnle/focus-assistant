"""Phone dock: the iPhone reports when it goes on or off its charger.

An iOS Shortcuts automation ("Charger is connected / disconnected") sends

    POST http://<pi>.local:8765/dock/in    (or /dock/out)
    Authorization: Bearer <FOCUS_DOCK_TOKEN>

It also serves the display page (focus.display) for a browser at
GET /display?k=<FOCUS_DISPLAY_TOKEN>, read-only. The page polls it with
If-None-Match and gets a bodyless 304 until something changes.

It answers private (home network) addresses with the right token and
nothing else; it can only append dock events to the state file and read it
for the page. It never holds the API key.

Only focus time counts. If the phone comes off the charger during a focus
block (FOCUS_BLOCKS, weekdays) and stays off for FOCUS_DOCK_SETTLE seconds,
PHONE_OUT is logged at the moment it came off; putting it back logs
PHONE_IN with how long it was gone. A quick glance at a notification is
ignored.

    python -m focus.inputs.dock    the service (what focus-dock runs)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Callable

from .. import actions, blocks as focus_blocks, config, display, state as st
from ..blocks import DEFAULT_BLOCKS, Block, in_block, parse_blocks  # noqa: F401  (tests use these here)
from ..config import Config
from . import _web

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765

# (delay seconds, callback) -> something with .cancel(); threading.Timer in production.
Scheduler = Callable[[float, Callable[[], None]], object]


def _start_timer(delay: float, fn: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer


class Dock:
    """Turns charger on/off reports into PHONE_OUT / PHONE_IN events."""

    def __init__(self, cfg: Config, blocks: list[Block], settle: float,
                 schedule: Scheduler = _start_timer):
        self.cfg = cfg
        self.blocks = blocks
        self.settle = settle
        self.schedule = schedule
        self._lock = threading.Lock()
        self._out_at: datetime | None = None  # phone is off the charger since
        self._logged = False                  # PHONE_OUT written for this lift
        self._pending = None

    def phone_out(self, now: datetime) -> None:
        with self._lock:
            if self._out_at is not None:
                log.info("Charger: out (already out, ignored)")
                return
            self._out_at = now
            self._logged = False
            if not in_block(now, self.blocks):
                log.info("Charger: out (outside focus blocks, not counted)")
                return
            log.info("Charger: out (counts if it stays off %g s)", self.settle)
            self._pending = self.schedule(self.settle, lambda: self._confirm(now))

    def _confirm(self, out_at: datetime) -> None:
        with self._lock:
            if self._out_at != out_at or self._logged:
                return  # it came back before settling
            with st.locked_state(self.cfg, today=out_at.date()) as s:
                actions.log_event(s, st.PHONE_OUT, at=out_at)
            self._logged = True
            log.info("Phone out since %s", out_at.strftime("%H:%M"))

    def phone_in(self, now: datetime) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.cancel()
                self._pending = None
            out_at, logged = self._out_at, self._logged
            self._out_at, self._logged = None, False
            # Only close an out that was logged today.
            if not logged or out_at is None or out_at.date() != now.date():
                log.info("Charger: in (nothing to close)")
                return
            minutes = round((now - out_at).total_seconds() / 60)
            with st.locked_state(self.cfg, today=now.date()) as s:
                actions.log_event(s, st.PHONE_IN, f"{minutes} min")
            log.info("Phone back after %d min", minutes)


def make_handler(dock: Dock, token: str, display_token: str | None = None) -> type[BaseHTTPRequestHandler]:
    routes = {"/dock/in": dock.phone_in, "/dock/out": dock.phone_out}
    page = display.Page(dock.cfg, dock.blocks) if display_token is not None else None

    class Handler(BaseHTTPRequestHandler):
        timeout = 10
        # Keep-alive: the page polls every 2 s over one connection, not a new one each time.
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            # The display page lives here because this service is local-only and keyless.
            if display_token is None or _web.path_of(self) != "/display":
                self.send_error(404)
                return
            if not _web.authorized(self, display_token, in_query=True):
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
            if not _web.authorized(self, token):
                return
            # Dock reports carry no body, but read any so keep-alive stays in step.
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65536:
                self.send_error(413)
                return
            self.rfile.read(length)
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            route(datetime.now())
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            log.debug("%s " + format, self.client_address[0], *args)

    return Handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    token = _web.token_from_env("FOCUS_DOCK_TOKEN")
    if token is None:
        return 1
    dock = Dock(
        config.load(),
        focus_blocks.from_env(),
        float(os.environ.get("FOCUS_DOCK_SETTLE", "30")),
    )
    # Optional: without a display token the page is simply not served.
    display_token = os.environ.get("FOCUS_DISPLAY_TOKEN") or None
    _web.serve(make_handler(dock, token, display_token), os.environ.get("FOCUS_DOCK_HOST", "0.0.0.0"),
               int(os.environ.get("FOCUS_DOCK_PORT", DEFAULT_PORT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
