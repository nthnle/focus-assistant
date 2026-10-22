import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from focus import config
from focus.inputs import _web, chat

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
    ("100.64.1.1", None, False),                   # carrier-grade NAT is not home
    ("not-an-ip", None, False),
    ("192.168.1.50", "192.168.1.50", True),
    ("192.168.1.51", "192.168.1.50", False),
    ("192.168.1.51", "10.0.0.0/8, 192.168.1.0/24", True),
    ("8.8.8.8", "8.8.8.8", False),                 # listing a public address doesn't open it
    ("127.0.0.1", "192.168.1.50", True),           # the Pi itself, for curl tests
    ("::1", "192.168.1.50", True),
])
def test_client_allowed(monkeypatch, address, allowed, ok):
    if allowed:
        monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", allowed)
    else:
        monkeypatch.delenv("FOCUS_ALLOWED_CLIENTS", raising=False)
    assert _web.client_allowed(address, _web.allowed_clients()) is ok


def test_bad_allowlist_entry_refuses_to_start(monkeypatch):
    monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", "192.168.1.50, my-phone")
    with pytest.raises(ValueError):
        _web.serve(BaseHTTPRequestHandler, "127.0.0.1", 0)


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("FOCUS_ALLOWED_CLIENTS", raising=False)
    calls = []

    def run(cfg, text):
        calls.append(text)
        return "ok"

    srv = ThreadingHTTPServer(("127.0.0.1", 0), chat.make_handler(config.load(), TOKEN, run=run))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", calls
    srv.shutdown()
    srv.server_close()


def raw_post(url, headers, body=b""):
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    conn.putrequest("POST", parts.path)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders(body)
    status = conn.getresponse().status
    conn.close()
    return status


def test_allowlist_applies_to_live_requests(base, monkeypatch):
    url, calls = base
    body = b'{"text": "hi"}'
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Length": str(len(body))}
    monkeypatch.setenv("FOCUS_ALLOWED_CLIENTS", "192.168.1.50")
    assert raw_post(f"{url}/chat", auth, body) == 200   # loopback: always allowed
    assert raw_post(f"{url}/chat", {"Content-Length": str(len(body))}, body) == 403   # but still needs the token
    assert calls == ["hi"]


def test_malformed_content_length_is_answered_not_crashed(base):
    url, calls = base
    # Without a token: a plain 403, not an exception in the handler.
    assert raw_post(f"{url}/chat", {"Content-Length": "abc"}) == 403
    # With one: a 400 instead of blocking on read(-1).
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert raw_post(f"{url}/chat", {**auth, "Content-Length": "-1"}) == 400
    assert raw_post(f"{url}/chat", {**auth, "Content-Length": "abc"}) == 400
    assert calls == []


def test_words_are_kept_out_of_logs_by_default(monkeypatch):
    monkeypatch.delenv("FOCUS_LOG_WORDS", raising=False)
    assert config.words("call the dentist") == "<16 chars>"
    monkeypatch.setenv("FOCUS_LOG_WORDS", "1")
    assert config.words("call the dentist") == "'call the dentist'"
