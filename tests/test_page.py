import threading
import urllib.error
import urllib.request
from datetime import datetime, time
from http.server import ThreadingHTTPServer

import pytest

from focus import actions, blocks, config, display, presence, state as st
from focus.inputs import page

BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)
MONDAY_10 = datetime(2026, 10, 12, 10, 0)
TOKEN = "d" * 24


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def test_parse_blocks():
    assert BLOCKS == [(time(9), time(12)), (time(13), time(17, 30))]
    assert blocks.parse_blocks(" 08:30-10:00 , ") == [(time(8, 30), time(10))]
    with pytest.raises(ValueError):
        blocks.parse_blocks("9-12")
    with pytest.raises(ValueError):
        blocks.parse_blocks("12:00-09:00")


def test_in_block_is_weekdays_inside_windows():
    assert blocks.in_block(MONDAY_10, BLOCKS)
    assert not blocks.in_block(MONDAY_10.replace(hour=12, minute=30), BLOCKS)
    assert not blocks.in_block(MONDAY_10.replace(hour=12), BLOCKS)  # end is exclusive
    assert not blocks.in_block(datetime(2026, 10, 17, 10, 0), BLOCKS)  # Saturday


@pytest.fixture
def server(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write <spec>")
        s.checkins.append({"slot": "midday", "at": "2026-10-16T13:00:00-05:00", "reply": "Keep going."})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), page.make_handler(cfg, BLOCKS, TOKEN))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def test_page_needs_its_token(server):
    status, html = get(f"{server}/display?k={TOKEN}")
    assert status == 200
    assert "MIDDAY CHECK-IN" in html
    assert "Write &lt;spec&gt;" in html
    assert "Keep going." in html
    assert get(f"{server}/display?k={'x' * 24}")[0] == 403
    assert get(f"{server}/display")[0] == 403
    assert get(f"{server}/other?k={TOKEN}")[0] == 404


def test_nothing_can_be_posted(server):
    """The charger reports are gone: the page service only reads."""
    req = urllib.request.Request(f"{server}/dock/out", method="POST", data=b"",
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 501


def get_if(url, etag):
    req = urllib.request.Request(url, headers={"If-None-Match": etag})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("ETag"), resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("ETag"), ""


def test_page_answers_304_until_the_content_changes(server, cfg):
    url = f"{server}/display?k={TOKEN}"
    status, etag, html = get_if(url, '"stale"')
    assert status == 200
    assert f"let version = {etag}" in html  # the page knows its own version
    assert get_if(url, etag)[:2] == (304, etag)
    assert get_if(f"{server}/display", etag)[0] == 403

    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Email Sam")
    status, etag2, html = get_if(url, etag)
    assert status == 200 and etag2 != etag
    assert "Email Sam" in html


def test_page_renders_only_when_its_inputs_change(cfg, monkeypatch):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write the spec")
    renders = []
    real = display.render
    monkeypatch.setattr(display, "render", lambda *a: renders.append(a) or real(*a))
    p = display.Page(cfg, BLOCKS)
    first = p.get("2026-10-16T10:20:05-05:00")
    assert p.get("2026-10-16T10:20:40-05:00") == first  # same minute, same files
    assert len(renders) == 1
    p.get("2026-10-16T10:21:00-05:00")                   # the minute moved
    assert len(renders) == 2
    presence.sound_path(cfg).write_text('{"sound_at": "2026-10-16T10:21:00-05:00"}')
    p.get("2026-10-16T10:21:30-05:00")                   # presence.json changed
    assert len(renders) == 3
    when = datetime.fromisoformat("2026-10-16T10:21:40-05:00")
    presence.record_camera(cfg, "work", when, when, sees_away=True)
    p.get("2026-10-16T10:21:45-05:00")                   # camera.json changed
    assert len(renders) == 4
    assert renders[-1][3] is BLOCKS                      # the service's own blocks
