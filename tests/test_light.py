import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from focus import actions, chat, cli, config, display, light, presence, state as st, status
from focus.inputs import buttons, page

TZ = timezone(timedelta(hours=-6))
EVENING = datetime(2026, 11, 10, 19, 0, tzinfo=TZ)
SPAN = (time(8), time(0))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    for name in ("FOCUS_LIGHT_HOURS", "FOCUS_LIGHT_LUX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOCUS_LIGHT_PIN", "12")
    return config.load()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# --- the rules -----------------------------------------------------------------------

def test_hours_can_run_past_midnight(monkeypatch):
    assert light.hours() == SPAN
    assert light.in_hours(time(23, 59), *SPAN) and not light.in_hours(time(0, 30), *SPAN)
    assert light.in_hours(time(10), time(9), time(17)) and not light.in_hours(time(17), time(9), time(17))
    assert light.in_hours(time(3), time(6), time(6))           # the same time twice: all day
    monkeypatch.setenv("FOCUS_LIGHT_HOURS", "8-23")
    with pytest.raises(ValueError):
        light.hours()


def test_no_pin_no_light(monkeypatch):
    monkeypatch.delenv("FOCUS_LIGHT_PIN", raising=False)
    assert not light.enabled() and not light.start(None)
    monkeypatch.setenv("FOCUS_LIGHT_PIN", "twelve")
    assert not light.enabled()


def test_the_room_turns_dim_and_bright_without_flickering():
    clock = Clock()
    room = light.Room(30, settle_s=30, clock=clock)
    assert room.update(None) is None                  # nothing read yet
    assert room.update(200) is False                  # the first reading counts at once
    assert room.update(20) is False                   # dim, but not for long enough
    clock.t = 29
    assert room.update(20) is False
    clock.t = 30
    assert room.update(20) is True
    clock.t = 100
    assert room.update(45) is True                    # between 30 and 60: stays dim
    assert room.update(70) is True                    # bright, starting now
    clock.t = 110
    assert room.update(25) is True                    # a dip resets the wait
    assert room.update(70) is True
    clock.t = 140
    assert room.update(70) is False
    assert room.update(None) is False                 # a failed read changes nothing


@pytest.mark.parametrize("manual, dim, at, away, expected", [
    (None, True, EVENING, False, (True, "the room is dim")),
    (None, False, EVENING, False, (False, "the room is bright")),
    (None, True, EVENING, True, (False, "you're away")),
    (None, True, EVENING.replace(hour=1), False, (False, "outside its hours")),
    (None, None, EVENING, False, (False, "no reading from the light sensor")),
    ("on", False, EVENING.replace(hour=1), True, (True, "on by hand")),
    ("off", True, EVENING, False, (False, "off by hand")),
])
def test_decide(manual, dim, at, away, expected):
    assert light.decide(manual, dim, at, away, SPAN) == expected


# --- the loop, with a fake sensor and switch -----------------------------------------

class FakeSwitch:
    def __init__(self):
        self.calls = []

    def set(self, on):
        self.calls.append(on)


class FakeSensor:
    def __init__(self, readings):
        self.readings = list(readings)

    def read(self):
        return self.readings.pop(0) if self.readings else None


def run(cfg, readings, now=EVENING, steps=None, set_light=None):
    switch, clock = FakeSwitch(), Clock()
    steps = len(readings) if steps is None else steps
    count = iter(range(steps + 1))

    def sleep(s):
        clock.t += s
        if set_light and clock.t == set_light[0]:
            with st.locked_state(cfg, today=now.date()) as s_:
                actions.set_light(s_, set_light[1])

    light.run(cfg, switch, FakeSensor(readings), room=light.Room(30, settle_s=4, clock=clock), span=SPAN,
              now=lambda: now, clock=clock, sleep=sleep, running=lambda: next(count) < steps)
    return switch.calls


def test_the_loop_switches_only_on_changes_and_records_why(cfg):
    calls = run(cfg, [10, 10, 10, 200, 200, 200, 200])
    assert calls == [True, False]                     # on at once, off after 4 s of bright
    seen = light.read(cfg)
    assert seen["on"] is False and seen["why"] == "the room is bright" and seen["lux"] == 200


def test_a_manual_setting_wins_and_auto_hands_back(cfg):
    calls = run(cfg, [10] * 6, set_light=(4, "off"))
    assert calls == [True, False]
    assert light.read(cfg)["why"] == "off by hand"


def test_the_camera_seeing_you_away_turns_it_off(cfg):
    presence.record_camera(cfg, "away", EVENING - timedelta(minutes=10), EVENING, sees_away=True)
    assert run(cfg, [10, 10]) == [False]
    assert light.read(cfg)["why"] == "you're away"


def test_toggle_flips_what_the_light_is_doing(cfg):
    light.record(cfg, True, "the room is dim", 10, EVENING)
    assert light.toggle(cfg, EVENING) == "Light off until midnight."
    assert st.peek(cfg, today=EVENING.date()).light == "off"
    light.record(cfg, False, "off by hand", 10, EVENING)
    assert light.toggle(cfg, EVENING) == "Light on until midnight."


# --- state ---------------------------------------------------------------------------

def test_a_manual_setting_ends_at_midnight(cfg):
    with st.locked_state(cfg, today=date(2026, 11, 10)) as s:
        assert actions.set_light(s, "on") == "on" and s.light == "on"
        with pytest.raises(st.StateError):
            actions.set_light(s, "dim")
    assert st.peek(cfg, today=date(2026, 11, 10)).light == "on"
    assert st.peek(cfg, today=date(2026, 11, 11)).light is None
    with st.locked_state(cfg, today=date(2026, 11, 10)) as s:
        actions.set_light(s, "auto")
        assert s.light is None


def test_a_bad_value_in_the_file_reads_as_auto(cfg):
    data = st.State(date="2026-11-10").to_dict() | {"light": "blinking"}
    assert st.State.from_dict(data).light is None


# --- the ways to set it ----------------------------------------------------------------

def test_focus_light(cfg, capsys, monkeypatch):
    assert cli.main(["light", "off"]) == 0
    assert "Light off until midnight" in capsys.readouterr().out
    assert st.peek(cfg).light == "off"
    light.record(cfg, False, "off by hand", 12, EVENING)
    assert cli.main(["light"]) == 0
    assert "off (off by hand, 12 lux); set off by hand until midnight" in capsys.readouterr().out
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    assert cli.main(["light", "on"]) == 1


def test_voice_can_set_it_only_with_a_light(cfg, monkeypatch):
    text, is_error = chat._run_tool(cfg, "set_light", json.dumps({"mode": "on"}))
    assert not is_error and st.peek(cfg).light == "on"
    assert chat._run_tool(cfg, "set_light", json.dumps({"mode": "purple"}))[1]
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    text, is_error = chat._run_tool(cfg, "set_light", json.dumps({"mode": "off"}))
    assert is_error and "no front light" in text
    assert "set_light" in [t["function"]["name"] for t in chat.TOOLS]


def test_the_page_sets_it(cfg, monkeypatch):
    assert page.edit_list(cfg, {"op": "light", "mode": "off"}) == "light off"
    assert st.peek(cfg).light == "off"
    with pytest.raises(page.BadRequest):
        page.edit_list(cfg, {"op": "light", "mode": "disco"})
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    with pytest.raises(page.BadRequest):
        page.edit_list(cfg, {"op": "light", "mode": "on"})


def test_the_page_shows_it(cfg, monkeypatch):
    s = st.State(date="2026-11-10", light="off")
    assert "waiting for focus-display" in display.light_controls(cfg, s)
    light.record(cfg, False, "off by hand", 12, EVENING)
    html = display.light_controls(cfg, s)
    assert '<button data-op="light" data-mode="off" aria-pressed="true">Off</button>' in html
    assert 'data-mode="auto" aria-pressed="false"' in html and "off by hand" in html
    assert ("Light", "Off by hand", display.PLAIN) in display.day_status(s, EVENING, cfg, [])
    page_html = display.render_html(s, EVENING.isoformat(), cfg, [])
    assert "Front light" in page_html
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    assert display.light_controls(cfg, s) == ""
    assert "Front light" not in display.render_html(s, EVENING.isoformat(), cfg, [])


def test_status_has_a_light_line_only_with_a_light(cfg, monkeypatch):
    s = st.State(date="2026-11-10")
    light.record(cfg, True, "the room is dim", 8, EVENING)
    assert "Light        on (the room is dim, 8 lux)" in status.report(cfg, s, EVENING, lambda *a: "active")
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    assert "Light  " not in status.report(cfg, s, EVENING, lambda *a: "active")


# --- the D button's long press ------------------------------------------------------------

def test_presses_count_on_release_and_long_ones_say_so():
    s = 1_000_000_000
    edges = [(3, True, 0), (3, False, s // 5),        # D tapped
             (3, True, 2 * s), (3, False, 3 * s),     # D held a second
             (0, False, 4 * s),                       # A up, never seen down: skipped
             (0, True, 5 * s), (0, False, 7 * s)]     # A held: still A's job
    assert list(buttons.presses_from_edges(iter(edges))) == ["D", "D-long", "A-long"]


def test_a_long_press_on_d_toggles_the_light(cfg, monkeypatch):
    light.record(cfg, False, "the room is bright", 300, EVENING)
    assert buttons.press(cfg, "D-long", EVENING) == "Light on until midnight."
    assert buttons.press(cfg, "D", EVENING) == "Redrawing."
    monkeypatch.delenv("FOCUS_LIGHT_PIN")
    assert buttons.press(cfg, "D-long", EVENING) == "Redrawing."     # no light: just D
