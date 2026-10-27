from datetime import datetime, timedelta, timezone

import pytest

from focus import actions, config, display, state as st
from focus.inputs import buttons

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def test_c_pauses_for_half_an_hour_then_resumes(cfg):
    assert buttons.press(cfg, "C", NOW) == "Paused until 10:30."
    s = st.peek(cfg, today=NOW.date())
    assert s.paused_until == "2026-10-12T10:30:00-05:00"
    assert display.panel_note(s, NOW) == "Paused until 10:30"
    assert buttons.press(cfg, "C", NOW + timedelta(minutes=5)) == "Resumed."
    assert st.peek(cfg, today=NOW.date()).paused_until is None


def test_a_marks_the_suggested_item_done_and_b_undoes_it(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
        email = actions.add_priority(s, "Email Sam")
        s.checkins.append({"slot": "morning", "at": "2026-10-12T09:00:00-05:00",
                           "reply": "Start with Email Sam.", "start_id": email.id})
    assert buttons.press(cfg, "A", NOW) == "Done: Email Sam. 1 left on your list."
    assert buttons.press(cfg, "B", NOW) == "Undone: Email Sam is back on your list."


def test_d_only_redraws(cfg):
    assert buttons.press(cfg, "D", NOW) == "Redrawing."
    assert st.peek(cfg).events == []


def test_pins(monkeypatch):
    assert buttons.pins() == [5, 6, 16, 24]
    monkeypatch.setenv("FOCUS_INKY_BUTTONS", "5,6,16,25")
    assert buttons.pins() == [5, 6, 16, 25]
    monkeypatch.setenv("FOCUS_INKY_BUTTONS", "")
    assert buttons.pins() == []


def test_presses_drop_bounces_and_survive_a_failing_reader():
    times = iter([0.0, 0.1, 0.2, 1.0])

    def source():
        yield from "AABA"
        raise OSError("no gpiochip")   # logged; the thread just ends

    presses = buttons.Presses(source(), clock=lambda: next(times))
    got = []
    while len(got) < 3:
        got += presses.wait(1.0)
    assert got == ["A", "B", "A"]          # the second A, 0.1 s after the first, was a bounce
    assert presses.wait(0.01) == []
