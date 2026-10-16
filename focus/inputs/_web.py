"""What the phone-facing services share: the token check and the server loop.

Only private (home network) addresses with `Authorization: Bearer <token>`
get past `authorized()`; everything else is a 403 and a log line.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 16


def token_from_env(name: str) -> str | None:
    token = os.environ.get(name, "")
    if len(token) < MIN_TOKEN_LENGTH:
        print(f"set {name} (at least {MIN_TOKEN_LENGTH} characters)", file=sys.stderr)
        return None
    return token


def authorized(handler: BaseHTTPRequestHandler, token: str) -> bool:
    try:
        private = ipaddress.ip_address(handler.client_address[0]).is_private
    except ValueError:
        private = False
    given = handler.headers.get("Authorization", "").encode()
    if private and hmac.compare_digest(given, f"Bearer {token}".encode()):
        return True
    log.warning("Rejected %s %s from %s", handler.command, handler.path, handler.client_address[0])
    handler.send_error(403)
    return False


def serve(handler: type[BaseHTTPRequestHandler], host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), handler)
    log.info("Listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
