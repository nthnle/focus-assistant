import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta
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


def test_the_page_wont_start_without_its_token(monkeypatch, capsys):
    monkeypatch.delenv("FOCUS_DISPLAY_TOKEN", raising=False)
    assert page.main() == 1
    assert "FOCUS_DISPLAY_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("FOCUS_DISPLAY_TOKEN", "short")
    assert page.main() == 1


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


def post(url, body, content_type="application/json"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, method="POST", data=data, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw.startswith(b"{") else {}


def test_the_editor_changes_the_list(server, cfg):
    url = f"{server}/display/list?k={TOKEN}"
    assert post(url, {"op": "add", "text": "  Email\n  Sam "}) == (200, {"ok": True})
    s = st.peek(cfg)
    spec, sam = s.priorities
    assert sam.text == "Email Sam"  # one line, however it was pasted

    assert post(url, {"op": "rename", "id": spec.id, "text": "Write the spec"})[0] == 200
    assert post(url, {"op": "done", "id": spec.id})[0] == 200
    assert st.peek(cfg).find(spec.id).done
    assert post(url, {"op": "reopen", "id": spec.id})[0] == 200
    assert post(url, {"op": "move", "id": sam.id, "on": "later"})[0] == 200
    s = st.peek(cfg)
    assert s.find(spec.id).text == "Write the spec" and not s.find(spec.id).done
    assert s.find(sam.id).on == st.LATER
    assert post(url, {"op": "move", "id": sam.id, "on": "today"})[0] == 200
    assert st.peek(cfg).find(sam.id).on is None
    assert post(url, {"op": "drop", "id": sam.id})[0] == 200
    assert [p.text for p in st.peek(cfg).priorities] == ["Write the spec"]

    # The page shows the change on its next poll.
    status, html = get(f"{server}/display?k={TOKEN}")
    assert 'data-op="rename" title="Tap to reword">Write the spec<' in html


def test_the_editor_says_what_went_wrong(server, cfg):
    url = f"{server}/display/list?k={TOKEN}"
    assert post(url, {"op": "done", "id": 99}) == (400, {"error": "No priority with id 99"})
    assert post(url, {"op": "done", "id": True})[0] == 400
    assert post(url, {"op": "add", "text": "   "})[0] == 400
    assert post(url, {"op": "add", "text": "x" * 201})[0] == 400
    assert post(url, {"op": "move", "id": 1, "on": "2026-01-01"})[0] == 400  # not after today
    assert post(url, {"op": "move", "id": 1, "on": "soon"}) == (400, {"error": "Which day?"})
    assert post(url, {"op": "wipe"})[0] == 400
    assert post(url, ["add"])[0] == 400
    assert post(url, b"{not json")[0] == 400
    assert [p.text for p in st.peek(cfg).priorities] == ["Write <spec>"]


def test_only_the_page_can_post(server, cfg):
    change = {"op": "add", "text": "Sneaky"}
    assert post(f"{server}/display/list?k={'x' * 24}", change)[0] == 403
    assert post(f"{server}/display/list", change)[0] == 403
    # A form on another site can't send JSON without the browser asking first.
    assert post(f"{server}/display/list?k={TOKEN}", b"op=add&text=Sneaky",
                "application/x-www-form-urlencoded")[0] == 415
    assert post(f"{server}/display/list?k={TOKEN}", {"text": "x" * 5000})[0] == 413
    assert post(f"{server}/dock/out?k={TOKEN}", change)[0] == 404
    assert [p.text for p in st.peek(cfg).priorities] == ["Write <spec>"]


def test_editor_rows(cfg):
    with st.locked_state(cfg, date(2026, 10, 16)) as s:  # a Friday
        a = actions.add_priority(s, "Write <spec>")
        b = actions.add_priority(s, "Email Sam")
        c = actions.add_priority(s, "Plan trip", st.LATER)
        actions.complete_priority(s, b.id)
    html = display.editor(st.peek(cfg, date(2026, 10, 16)))
    assert f'<li data-id="{a.id}"><button class="tick" data-op="done"' in html
    assert "Write &lt;spec&gt;" in html
    assert '<option value="2026-10-19">To Mon 19 Oct</option>' in html  # Friday's next workday
    assert f'<li class="done" data-id="{b.id}">' in html and "Done today" in html
    assert f'<li data-id="{c.id}"><span class="t" data-op="rename" title="Tap to reword">Plan trip</span>' in html
    menus = html.split('<select data-op="move" data-min="2026-10-17" aria-label="Move">')[1:]
    today_menu, later_menu = (m.split("</select>")[0] for m in menus)
    # Today's item can go to Monday, later or a picked day; the later one back to today.
    assert '"today"' not in today_menu and '"later"' in today_menu and '"pick"' in today_menu
    assert html.count('<option value="pick">Pick a day…</option>') == 2
    assert '<span class="when">someday</span>' in html
    assert '<option value="today">To today</option>' in later_menu and '"later"' not in later_menu


def test_a_picked_day_moves_the_item(server, cfg):
    url = f"{server}/display/list?k={TOKEN}"
    pid = st.peek(cfg).priorities[0].id
    tomorrow = date.today() + timedelta(days=1)
    assert post(url, {"op": "move", "id": pid, "on": (tomorrow + timedelta(days=10)).isoformat()})[0] == 200
    assert st.peek(cfg).find(pid).on == (tomorrow + timedelta(days=10)).isoformat()
    assert post(url, {"op": "move", "id": pid, "on": date.today().isoformat()}) == (
        400, {"error": "Pick a day after today, or bring it back to today instead"})




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
    when = datetime.fromisoformat("2026-10-16T10:21:40-05:00")
    presence.record_camera(cfg, "work", when, when, sees_away=True)
    p.get("2026-10-16T10:21:45-05:00")                   # camera.json changed
    assert len(renders) == 3
    assert renders[-1][3] is BLOCKS                      # the service's own blocks
