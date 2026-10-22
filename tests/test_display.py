import pytest

from focus import display
from focus.state import Priority, State


def test_open_items_with_carried_tag():
    s = State(date="2026-10-16", priorities=[
        Priority(1, "Done one", done_at="x"),
        Priority(2, "Spec", carried_days=3),
        Priority(3, "Email"), Priority(4, "Report"), Priority(5, "Fourth open"),
    ])
    page = display.render_html(s, "2026-10-16T09:05:00-05:00")
    assert "Spec" in page and '<span class="c">3d</span>' in page
    assert "Report" in page and "Fourth open" in page and "Done one" not in page
    assert "more</li>" not in page
    assert "NO CHECK-IN YET" in page
    assert "Fri 16 Oct" in page and "Updated 09:05" in page


def test_empty_state():
    page = display.render_html(State(date="2026-10-16"), "2026-10-16T09:05:00-05:00")
    assert "Nothing open on the list" in page


@pytest.mark.parametrize("n, listed, more", [(6, 6, 0), (7, 5, 2), (10, 5, 5)])
def test_a_long_list_ends_in_more(n, listed, more):
    s = State(date="2026-10-16", priorities=[Priority(i, f"Item {i}") for i in range(1, n + 1)])
    items, extra = display.shown(s)
    assert (len(items), extra) == (listed, more)
    page = display.render_html(s, "2026-10-16T09:05:00-05:00")
    assert page.count('<span class="d">') == listed
    assert (f"+{more} more" in page) == bool(more)


from datetime import datetime, timedelta, timezone  # noqa: E402



from focus import blocks, config, presence, state as st  # noqa: E402

TZ = timezone(timedelta(hours=-5))
BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)


def mon(h, m):
    return datetime(2026, 10, 12, h, m, tzinfo=TZ)


@pytest.mark.parametrize("when, expected", [
    (mon(10, 20), [("Now", "Focus until 12:00", "alert"), ("Next break", "10:50", "plain")]),
    (mon(10, 55), [("Now", "Break till 11:00", "good")]),
    (mon(11, 55), [("Now", "Break till 12:00", "good")]),
    (mon(17, 10), [("Now", "Focus until 17:30", "alert"), ("Next break", "Block ends 17:30", "plain")]),
    (mon(12, 30), [("Now", "Next block at 13:00", "plain")]),
    (mon(8, 15), [("Now", "Starts at 09:00", "plain")]),
    (mon(18, 0), [("Now", "Done for the day", "good")]),
    (datetime(2026, 10, 17, 10, 0, tzinfo=TZ), [("Now", "Weekend", "plain")]),
], ids=["in block", "break", "last break before lunch", "late in block", "lunch", "morning", "evening", "saturday"])
def test_where_in_day(when, expected):
    assert display.where_in_day(when, BLOCKS) == expected


def test_status_chips_for_phone_presence_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    cfg = config.load()
    presence.record_camera(cfg, "away", mon(10, 10), mon(10, 20), sees_away=True)
    s = State(date="2026-10-12", priorities=[Priority(1, "Spec", done_at="x"), Priority(2, "Email")])
    s.events = [{"at": mon(9, 50).isoformat(), "kind": st.STRETCH_REMINDER},
                {"at": mon(10, 0).isoformat(), "kind": st.PHONE_OUT}]
    s.checkins = [{"slot": "morning", "at": "x", "reply": "y", "unheard": True}]
    chips = dict((k, (v, tone)) for k, v, tone in display.day_status(s, mon(10, 20), cfg, BLOCKS))
    assert chips["Phone"] == ("In use since 10:00", "alert")
    assert chips["You"] == ("Away", "alert")            # the camera has seen the empty desk for 10 min
    assert chips["Check-in"] == ("Waiting for you", "alert")
    assert chips["Today"] == ("1 done, 1 open", "plain")
    assert chips["Phone pickups"] == ("1", "plain")
    assert chips["Breaks"] == ("1", "plain")


def test_phone_not_in_use_shows_only_while_the_camera_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    cfg = config.load()
    s = State(date="2026-10-12")
    off = dict((k, v) for k, v, _ in display.day_status(s, mon(10, 20), cfg, BLOCKS))
    assert "Phone" not in off and "You" not in off      # camera off, no sound: nothing to say
    presence.record_camera(cfg, "work", mon(10, 0), mon(10, 20), sees_away=True)
    on = dict((k, v) for k, v, _ in display.day_status(s, mon(10, 20), cfg, BLOCKS))
    assert on["Phone"] == "Not in use" and "You" not in on
    stale = dict((k, v) for k, v, _ in display.day_status(s, mon(10, 25), cfg, BLOCKS))
    assert "Phone" not in stale                          # the camera stopped writing


def test_page_includes_the_strip():
    page = display.render_html(State(date="2026-10-12"), mon(10, 20).isoformat(timespec="seconds"),
                               focus_blocks=BLOCKS)
    assert 'class="strip"' in page and "Focus until 12:00" in page
