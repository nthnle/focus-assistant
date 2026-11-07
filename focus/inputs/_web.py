"""The status page's network side: the token check and the server loop.

Only private (home network) addresses with `?k=<token>` in the URL get past
`authorized()`; everything else is a 403 and a log line. The token goes in
the URL because a bookmark can't send a header.

With Tailscale installed, install.sh sets FOCUS_TAILSCALE=1 and Tailscale's
addresses (100.64.0.0/10) count as home too: devices on your tailnet,
reaching the Pi over an encrypted network from anywhere. Without it they
don't, because internet providers also use that range for carrier-grade NAT.

Plain HTTP means anyone on the same Wi-Fi can read a token in transit, so
FOCUS_ALLOWED_CLIENTS (comma-separated addresses or networks, e.g. the
phone's reserved IP) narrows "home network" to just those devices.
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

# Tailscale gives each device an address here. It is carrier-grade NAT space,
# which Python doesn't count as private and some networks hand out to other
# customers, so it is trusted only when Tailscale is set up (FOCUS_TAILSCALE).
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def token_from_env(name: str) -> str | None:
    token = os.environ.get(name, "")
    if len(token) < MIN_TOKEN_LENGTH:
        print(f"set {name} (at least {MIN_TOKEN_LENGTH} characters)", file=sys.stderr)
        return None
    return token


def path_of(handler: BaseHTTPRequestHandler) -> str:
    return urlsplit(handler.path).path


def content_length(handler: BaseHTTPRequestHandler) -> int | None:
    """The request's Content-Length, or None if it is missing a number or negative."""
    raw = handler.headers.get("Content-Length") or "0"
    try:
        length = int(raw)
    except ValueError:
        return None
    return length if length >= 0 else None


def allowed_clients() -> list | None:
    """FOCUS_ALLOWED_CLIENTS as networks; None when unset (any private address).

    A bad entry raises, so the services refuse to start rather than run open.
    """
    raw = os.environ.get("FOCUS_ALLOWED_CLIENTS", "").strip()
    if not raw:
        return None
    return [ipaddress.ip_network(part.strip(), strict=False) for part in raw.split(",") if part.strip()]


def tailscale_on() -> bool:
    """FOCUS_TAILSCALE=1: install.sh found Tailscale on the Pi."""
    return os.environ.get("FOCUS_TAILSCALE", "") == "1"


def client_allowed(address: str, allowed: list | None, tailscale: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if not (ip.is_private or (tailscale and ip in TAILSCALE)):
        return False
    # The Pi itself is always allowed: loopback can't come from the network,
    # and it lets you test a service with curl on the Pi.
    return allowed is None or ip.is_loopback or any(ip in net for net in allowed)


def authorized(handler: BaseHTTPRequestHandler, token: str) -> bool:
    private = client_allowed(handler.client_address[0], allowed_clients(), tailscale_on())
    given = parse_qs(urlsplit(handler.path).query).get("k", [""])[0].encode()
    if private and hmac.compare_digest(given, token.encode()):
        return True
    # path_of() keeps a guessed token out of the log.
    log.warning("Rejected %s %s from %s", handler.command, path_of(handler), handler.client_address[0])
    # Read a small body first; replying with it unread can reset the connection,
    # and the client then sees a network error instead of the 403.
    length = content_length(handler) or 0
    if 0 < length <= 65536:
        handler.rfile.read(length)
    handler.send_error(403)
    return False


def serve(handler: type[BaseHTTPRequestHandler], host: str, port: int) -> None:
    allowed = allowed_clients()  # raises here, at startup, on a bad entry
    server = ThreadingHTTPServer((host, port), handler)
    log.info("Listening on %s:%d for %s", host, port,
             ", ".join(map(str, allowed)) if allowed else "any home-network address")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
