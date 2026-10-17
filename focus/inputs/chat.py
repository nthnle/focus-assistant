"""Chat with the coach from the phone: an iOS Shortcut (or Siri) sends

    POST http://<pi>.local:8766/chat
    Authorization: Bearer <FOCUS_CHAT_TOKEN>
    {"text": "I finished the spec, what next?"}

and gets the coach's reply back as plain text to show or speak. This is a
separate service from the dock because it holds the API key and talks to
the internet; the dock does neither.

    python -m focus.inputs.chat    the service (what focus-chat runs)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler

from .. import chat, config
from ..coach import CoachError
from ..config import Config
from . import _web

log = logging.getLogger(__name__)

DEFAULT_PORT = 8766
MAX_BODY = 8192


def make_handler(cfg: Config, token: str, daily_limit: int,
                 run=chat.run) -> type[BaseHTTPRequestHandler]:
    one_at_a_time = threading.Lock()  # keeps the day's chat history in order

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def _reply(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not _web.authorized(self, token):
                return
            if self.path != "/chat":
                self.send_error(404)
                return
            length = _web.content_length(self) or 0
            if not 0 < length <= MAX_BODY:
                self._reply(400, "Send a JSON body like {\"text\": \"...\"}")
                return
            try:
                text = json.loads(self.rfile.read(length))["text"]
                if not isinstance(text, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                self._reply(400, "Send a JSON body like {\"text\": \"...\"}")
                return

            log.info("Chat: %s", config.words(text[:80]))
            with one_at_a_time:
                try:
                    reply = run(cfg, text, daily_limit=daily_limit)
                except CoachError as exc:
                    log.warning("Chat failed: %s", exc)
                    # 200 so the Shortcut shows the reason instead of a generic error.
                    self._reply(200, f"Sorry, that didn't work: {exc}")
                    return
            self._reply(200, reply)

        def log_message(self, format: str, *args) -> None:
            log.debug("%s " + format, self.client_address[0], *args)

    return Handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    token = _web.token_from_env("FOCUS_CHAT_TOKEN")
    if token is None:
        return 1
    handler = make_handler(config.load(), token, int(os.environ.get("FOCUS_CHAT_DAILY_LIMIT", "50")))
    _web.serve(handler, os.environ.get("FOCUS_CHAT_HOST", "0.0.0.0"),
               int(os.environ.get("FOCUS_CHAT_PORT", DEFAULT_PORT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
