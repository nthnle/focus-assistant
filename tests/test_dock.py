import threading
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from http.server import ThreadingHTTPServer

import pytest

from focus import config, state as st
from focus.inputs import dock

BLOCKS = dock.parse_blocks(dock.DEFAULT_BLOCKS)
MONDAY_10 = datetime(2026, 10, 12, 10, 0)
TOKEN = "x" * 24


class FakeTimers:
    """Stands in for threading.Timer: fire() runs what is still pending."""

    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        timer = FakeTimer(fn)
        self.pending.append(timer)
        return timer

    def fire(self):
        for t in self.pending:
            if not t.cancelled:
                t.fn()
        self.pending = []


class FakeTimer:
    def __init__(self, fn):
        self.fn = fn
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


@pytest.fixture
def timers():
    return FakeTimers()


@pytest.fixture
def d(cfg, timers):
    return dock.Dock(cfg, BLOCKS, settle=30, schedule=timers)


def events(cfg, day=MONDAY_10):
    with st.locked_state(cfg, today=day.date()) as s:
        return s.events


def at(minutes, start=MONDAY_10):
    return start + timedelta(minutes=minutes)


def test_parse_blocks():
    assert BLOCKS == [(time(9), time(12)), (time(13), time(17, 30))]
    assert dock.parse_blocks(" 08:30-10:00 , ") == [(time(8, 30), time(10))]
    with pytest.raises(ValueError):
        dock.parse_blocks("9-12")
    with pytest.raises(ValueError):
        dock.parse_blocks("12:00-09:00")


def test_in_block_is_weekdays_inside_windows():
    assert dock.in_block(MONDAY_10, BLOCKS)
    assert not dock.in_block(MONDAY_10.replace(hour=12, minute=30), BLOCKS)
    assert not dock.in_block(MONDAY_10.replace(hour=12), BLOCKS)  # end is exclusive
    assert not dock.in_block(datetime(2026, 10, 17, 10, 0), BLOCKS)  # Saturday


def test_out_and_back_logs_both_with_the_real_out_time(cfg, d, timers):
    d.phone_out(MONDAY_10)
    timers.fire()
    d.phone_in(at(12))
    got = events(cfg)
    assert [e["kind"] for e in got] == [st.PHONE_OUT, st.PHONE_IN]
    assert got[0]["at"].startswith("2026-10-12T10:00:00")
    assert got[1]["detail"] == "12 min"


def test_quick_glance_logs_nothing(cfg, d, timers):
    d.phone_out(MONDAY_10)
    d.phone_in(MONDAY_10 + timedelta(seconds=10))
    timers.fire()
    assert events(cfg) == []


def test_repeated_reports_are_ignored(cfg, d, timers):
    d.phone_out(MONDAY_10)
    d.phone_out(at(1))
    timers.fire()
    d.phone_in(at(5))
    d.phone_in(at(6))
    assert [e["kind"] for e in events(cfg)] == [st.PHONE_OUT, st.PHONE_IN]
    assert events(cfg)[1]["detail"] == "5 min"


def test_outside_blocks_logs_nothing(cfg, d, timers):
    lunch = MONDAY_10.replace(hour=12, minute=15)
    d.phone_out(lunch)
    timers.fire()
    d.phone_in(at(25, lunch))
    assert events(cfg) == []


def test_charger_is_tracked_at_any_hour(cfg, d, timers):
    from focus import presence
    early = MONDAY_10.replace(hour=7, minute=30)
    d.phone_out(early)                                   # before the first block: no event
    assert events(cfg) == []
    assert presence.charger(cfg) == (False, early.astimezone())
    assert presence.phone_out_since(st.State(date="2026-10-12"), cfg) == early.astimezone()
    d.phone_in(at(5))
    assert presence.charger(cfg) == (True, at(5).astimezone())


def test_return_after_block_still_closes_the_out(cfg, d, timers):
    d.phone_out(MONDAY_10.replace(hour=11, minute=50))
    timers.fire()
    d.phone_in(MONDAY_10.replace(hour=12, minute=20))
    assert events(cfg)[-1]["detail"] == "30 min"


def test_out_from_yesterday_is_not_closed_today(cfg, d, timers):
    d.phone_out(MONDAY_10)
    timers.fire()
    tuesday = datetime(2026, 10, 13, 10, 0)
    d.phone_in(tuesday)
    assert events(cfg, tuesday) == []


@pytest.fixture
def server(cfg):
    calls = []

    class Recorder:
        def phone_in(self, now):
            calls.append("in")

        def phone_out(self, now):
            calls.append("out")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), dock.make_handler(Recorder(), TOKEN))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", calls
    srv.shutdown()
    srv.server_close()


def post(url, token=TOKEN):
    req = urllib.request.Request(url, method="POST", data=b"",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_http_routes_need_the_token(server):
    base, calls = server
    assert post(f"{base}/dock/out") == 204
    assert post(f"{base}/dock/in") == 204
    assert post(f"{base}/dock/in", token="wrong") == 403
    assert post(f"{base}/nope") == 404
    assert calls == ["out", "in"]


DISPLAY_TOKEN = "d" * 24


@pytest.fixture
def display_server(cfg):
    with st.locked_state(cfg) as s:
        from focus import actions
        actions.add_priority(s, "Write <spec>")
        s.checkins.append({"slot": "midday", "at": "2026-10-16T13:00:00-05:00", "reply": "Keep going."})
    d = dock.Dock(cfg, BLOCKS, settle=30, schedule=FakeTimers())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dock.make_handler(d, TOKEN, DISPLAY_TOKEN))
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


def test_display_page_needs_its_token(display_server):
    status, page = get(f"{display_server}/display?k={DISPLAY_TOKEN}")
    assert status == 200
    assert "MIDDAY CHECK-IN" in page
    assert "Write &lt;spec&gt;" in page
    assert "Keep going." in page
    assert get(f"{display_server}/display?k={TOKEN}")[0] == 403
    assert get(f"{display_server}/display")[0] == 403
    assert get(f"{display_server}/other?k={DISPLAY_TOKEN}")[0] == 404


def get_if(url, etag):
    req = urllib.request.Request(url, headers={"If-None-Match": etag})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("ETag"), resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("ETag"), ""


def test_display_answers_304_until_the_content_changes(display_server, cfg):
    url = f"{display_server}/display?k={DISPLAY_TOKEN}"
    status, etag, page = get_if(url, '"stale"')
    assert status == 200
    assert f"let version = {etag}" in page  # the page knows its own version
    assert get_if(url, etag)[:2] == (304, etag)
    assert get_if(f"{display_server}/display", etag)[0] == 403

    with st.locked_state(cfg) as s:
        from focus import actions
        actions.add_priority(s, "Email Sam")
    status, etag2, page = get_if(url, etag)
    assert status == 200 and etag2 != etag
    assert "Email Sam" in page


def test_page_renders_only_when_its_inputs_change(cfg, monkeypatch):
    from focus import actions, display, presence
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write the spec")
    renders = []
    real = display.render
    monkeypatch.setattr(display, "render", lambda *a: renders.append(a) or real(*a))
    page = display.Page(cfg, BLOCKS)
    first = page.get("2026-10-16T10:20:05-05:00")
    assert page.get("2026-10-16T10:20:40-05:00") == first  # same minute, same files
    assert len(renders) == 1
    page.get("2026-10-16T10:21:00-05:00")                  # the minute moved
    assert len(renders) == 2
    presence.sound_path(cfg).write_text('{"sound_at": "2026-10-16T10:21:00-05:00"}')
    page.get("2026-10-16T10:21:30-05:00")                  # presence.json changed
    assert len(renders) == 3
    assert renders[-1][3] is BLOCKS                        # the dock's own blocks
