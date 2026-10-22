"""The status page: the e-ink panel's layout in a browser, read-only.

    GET http://<pi>.local:8765/display?k=<FOCUS_DISPLAY_TOKEN>

The page (focus.display) polls with If-None-Match and gets a bodyless 304
until something changes. The service answers private (home network)
addresses with the right token and nothing else, only reads the state
folder, and never holds the API key.

    python -m focus.inputs.page    the service (what focus-page runs)
"""

from __future__ import annotations

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

from .. import blocks as focus_blocks, config, display, state as st
from ..blocks import Block
from ..config import Config
from . import _web

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765


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
