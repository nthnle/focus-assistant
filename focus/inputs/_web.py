"""What the phone-facing services share: the token check and the server loop.

Only private (home network) addresses with `Authorization: Bearer <token>`
get past `authorized()`; everything else is a 403 and a log line. A browser
page can't send that header, so it passes the token as `?k=<token>` instead.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 16


def token_from_env(name: str) -> str | None:
    token = os.environ.get(name, "")
    if len(token) < MIN_TOKEN_LENGTH:
        print(f"set {name} (at least {MIN_TOKEN_LENGTH} characters)", file=sys.stderr)
        return None
    return token


def path_of(handler: BaseHTTPRequestHandler) -> str:
    return urlsplit(handler.path).path


def authorized(handler: BaseHTTPRequestHandler, token: str, in_query: bool = False) -> bool:
    try:
        private = ipaddress.ip_address(handler.client_address[0]).is_private
    except ValueError:
        private = False
    if in_query:
        given = parse_qs(urlsplit(handler.path).query).get("k", [""])[0].encode()
        expected = token.encode()
    else:
        given = handler.headers.get("Authorization", "").encode()
        expected = f"Bearer {token}".encode()
    if private and hmac.compare_digest(given, expected):
        return True
    # path_of() keeps a guessed token out of the log.
    log.warning("Rejected %s %s from %s", handler.command, path_of(handler), handler.client_address[0])
    # Read a small body first; replying with it unread can reset the connection,
    # and the client then sees a network error instead of the 403.
    length = int(handler.headers.get("Content-Length") or 0)
    if 0 < length <= 65536:
        handler.rfile.read(length)
    handler.send_error(403)
    return False


def serve(handler: type[BaseHTTPRequestHandler], host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), handler)
    log.info("Listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
