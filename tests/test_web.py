import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from focus import blocks, config, state as st
from focus.inputs import _web, page

TOKEN = "x" * 24


class FakeHandler:
    def __init__(self, headers):
        self.headers = headers


@pytest.mark.parametrize("raw, expected", [
    (None, 0), ("0", 0), ("12", 12), ("-1", None), ("abc", None), ("1e3", None),
])
def test_content_length(raw, expected):
    headers = {} if raw is None else {"Content-Length": raw}
    assert _web.content_length(FakeHandler(headers)) == expected


@pytest.mark.parametrize("address, allowed, ok", [
    ("192.168.1.50", None, True),
    ("8.8.8.8", None, False),                      # never the internet
    ("not-an-ip", None, False),
    ("192.168.1.50", "192.168.1.50", True),
    ("192.168.1.51", "192.168.1.50", False),
    ("192.168.1.51", "10.0.0.0/8, 192.168.1.0/24", True),
    ("8.8.8.8", "8.8.8.8", False),                 # listing a public address doesn't open it
    ("127.0.0.1", "192.168.1.50", True),           # the Pi itself, for curl tests
    ("::1", "192.168.1.50", True),
    ("100.101.102.103", None, False),              # carrier-grade NAT, without Tailscale
])
def test_client_allowed(monkeypatch, address, allowed, ok):
    if allowed:
        monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", allowed)
    else:
        monkeypatch.delenv("FOCUS_ALLOWED_CLIENTS", raising=False)
    assert _web.client_allowed(address, _web.allowed_clients()) is ok


@pytest.mark.parametrize("address, allowed, ok", [
    ("100.101.102.103", None, True),               # a device on your tailnet
    ("100.128.0.1", None, False),                  # just past Tailscale's range
    ("100.101.102.103", "192.168.1.50", False),    # the allowlist narrows Tailscale too
    ("100.101.102.103", "192.168.1.50, 100.101.102.103", True),
])
def test_tailscale_addresses_only_with_tailscale(monkeypatch, address, allowed, ok):
    monkeypatch.setenv("FOCUS_TAILSCALE", "1")
    if allowed:
        monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", allowed)
    else:
        monkeypatch.delenv("FOCUS_ALLOWED_CLIENTS", raising=False)
    assert _web.client_allowed(address, _web.allowed_clients(), _web.tailscale_on()) is ok


def test_tailscale_is_off_unless_install_turned_it_on(monkeypatch):
    monkeypatch.delenv("FOCUS_TAILSCALE", raising=False)
    assert not _web.tailscale_on()
    monkeypatch.setenv("FOCUS_TAILSCALE", "yes")
    assert not _web.tailscale_on()


def test_bad_allowlist_entry_refuses_to_start(monkeypatch):
    monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", "192.168.1.50, my-phone")
    with pytest.raises(ValueError):
        _web.serve(BaseHTTPRequestHandler, "127.0.0.1", 0)


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("FOCUS_ALLOWED_CLIENTS", raising=False)
    cfg = config.load()
    handler = page.make_handler(cfg, blocks.parse_blocks(blocks.DEFAULT_BLOCKS), TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", cfg
    srv.shutdown()
    srv.server_close()


def raw_post(url, headers, body=b""):
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    conn.putrequest("POST", parts.path + (f"?{parts.query}" if parts.query else ""))
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders(body)
    status = conn.getresponse().status
    conn.close()
    return status


def items(cfg):
    return [p.text for p in st.peek(cfg).priorities]


def test_allowlist_applies_to_live_requests(base, monkeypatch):
    url, cfg = base
    body = b'{"op": "add", "text": "hi"}'
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", "192.168.1.50")
    assert raw_post(f"{url}/display/list?k={TOKEN}", headers, body) == 200   # loopback: always allowed
    assert raw_post(f"{url}/display/list", headers, body) == 403   # but still needs the token
    assert items(cfg) == ["hi"]


def test_malformed_content_length_is_answered_not_crashed(base):
    url, cfg = base
    # Without a token: a plain 403, not an exception in the handler.
    assert raw_post(f"{url}/display/list", {"Content-Length": "abc"}) == 403
    # With one: a 400 instead of blocking on read(-1).
    json_type = {"Content-Type": "application/json"}
    assert raw_post(f"{url}/display/list?k={TOKEN}", {**json_type, "Content-Length": "-1"}) == 400
    assert raw_post(f"{url}/display/list?k={TOKEN}", {**json_type, "Content-Length": "abc"}) == 400
    assert items(cfg) == []


def test_words_are_kept_out_of_logs_by_default(monkeypatch):
    monkeypatch.delenv("FOCUS_LOG_WORDS", raising=False)
    assert config.words("call the dentist") == "<16 chars>"
    monkeypatch.setenv("FOCUS_LOG_WORDS", "1")
    assert config.words("call the dentist") == "'call the dentist'"
