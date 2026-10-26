from datetime import date, datetime, time, timedelta, timezone

import pytest

from focus import actions, blocks, camera, checkin, coach, config, days, presence, schedule, state as st

TZ = timezone(timedelta(hours=-5))
MON = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
SAT = datetime(2026, 10, 17, 10, 0, tzinfo=TZ)
TIMES = schedule.Times((time(9), time(13), time(17, 30)), time(20))
BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    for name in ("FOCUS_WORKDAYS", "FOCUS_CHECKINS", "FOCUS_CHECKIN_UNTIL"):
        monkeypatch.delenv(name, raising=False)
    return config.load()


def at(hour, minute=0, day=SAT):
    return day.replace(hour=hour, minute=minute)


def ran(s, slot, when):
    s.checkins.append({"slot": slot, "at": when.isoformat(timespec="seconds"), "reply": "ok"})


def sitting(cfg, now, minutes=5, label="work", sees_away=True):
    presence.record_camera(cfg, label, now - timedelta(minutes=minutes), now, sees_away)


# --- work days ------------------------------------------------------------------

def test_parse_workdays():
    assert days.parse_workdays("mon-fri") == frozenset(range(5))
    assert days.parse_workdays("sun-thu") == frozenset({6, 0, 1, 2, 3})   # wraps
    assert days.parse_workdays("Mon, wed,friday") == frozenset({0, 2, 4})
    for bad in ("", "mo", "funday", "mon-xyz"):
        with pytest.raises(ValueError):
            days.parse_workdays(bad)


def test_a_bad_workdays_setting_stops_at_startup(cfg, monkeypatch):
    monkeypatch.setenv("FOCUS_WORKDAYS", "weekdays")
    with pytest.raises(ValueError):
        config.load()


def test_work_day_helpers(cfg, monkeypatch):
    fri, sat, sun = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
    assert days.next_workday(fri) == date(2026, 10, 19)
    assert days.last_workday_of_week(fri) and not days.last_workday_of_week(date(2026, 10, 15))
    assert not days.last_workday_of_week(sat)
    assert days.days_off_since(sun) == sat and days.days_off_since(fri) == fri
    monkeypatch.setenv("FOCUS_WORKDAYS", "mon-thu,sat")
    assert days.is_workday(sat) and not days.is_workday(fri)
    assert days.last_workday_of_week(sat) and not days.last_workday_of_week(date(2026, 10, 15))
    assert days.days_off_since(sun) == sun
    assert blocks.in_block(at(10), BLOCKS)                    # Saturday is a work day now
    assert not blocks.in_block(at(10, day=SAT - timedelta(days=1)), BLOCKS)


# --- when a check-in is due -------------------------------------------------------

def test_times_from_env(monkeypatch):
    assert schedule.from_env() == TIMES
    monkeypatch.setenv("FOCUS_CHECKINS", "08:30,12:30,17:00")
    monkeypatch.setenv("FOCUS_CHECKIN_UNTIL", "19:00")
    assert schedule.from_env() == schedule.Times((time(8, 30), time(12, 30), time(17)), time(19))
    for checkins, until in (("09:00,13:00", "20:00"), ("13:00,09:00,17:30", "20:00"),
                            ("09:00,13:00,17:30", "17:00"), ("9am,1pm,5pm", "20:00")):
        monkeypatch.setenv("FOCUS_CHECKINS", checkins)
        monkeypatch.setenv("FOCUS_CHECKIN_UNTIL", until)
        with pytest.raises(ValueError):
            schedule.from_env()


def test_the_slot_due_is_the_latest_one_open():
    s = st.State(date=SAT.date().isoformat())
    assert schedule.due(s, at(8, 59), TIMES) is None             # before the first
    assert schedule.due(s, at(9), TIMES) == "morning"
    assert schedule.due(s, at(12, 59), TIMES) == "morning"
    assert schedule.due(s, at(13), TIMES) == "midday"            # not a backlog: just the latest
    assert schedule.due(s, at(17, 30), TIMES) == "evening"
    assert schedule.due(s, at(20), TIMES) is None                # too late
    assert schedule.due(s, at(10), None) is None


def test_each_slot_once_and_an_hour_apart():
    s = st.State(date=SAT.date().isoformat())
    ran(s, "morning", at(12, 50))
    assert schedule.due(s, at(12, 55), TIMES) is None            # had it
    assert schedule.due(s, at(13, 10), TIMES) is None            # midday, but too soon
    assert schedule.due(s, at(13, 50), TIMES) == "midday"
    ran(s, "midday", at(13, 50))
    assert schedule.due(s, at(16), TIMES) is None


def test_a_checkin_due_in_a_pause_waits_for_it_to_end():
    s = st.State(date=SAT.date().isoformat())
    actions.pause(s, at(11))
    assert schedule.due(s, at(10), TIMES) is None
    assert schedule.due(s, at(11, 1), TIMES) == "morning"


def test_at_desk_once_the_camera_has_seen_you_a_while(cfg):
    now = at(10)
    assert not presence.at_desk(cfg, now, schedule.SETTLE)       # no camera
    sitting(cfg, now, minutes=1)
    assert not presence.at_desk(cfg, now, schedule.SETTLE)       # just walked past
    sitting(cfg, now, minutes=3)
    assert presence.at_desk(cfg, now, schedule.SETTLE)
    sitting(cfg, now, minutes=3, label="phone")
    assert presence.at_desk(cfg, now, schedule.SETTLE)           # on the phone, at the desk
    sitting(cfg, now, minutes=30, label="away")
    assert not presence.at_desk(cfg, now, schedule.SETTLE)
    sitting(cfg, now, minutes=30, sees_away=False)
    assert not presence.at_desk(cfg, now, schedule.SETTLE)       # can't tell an empty desk
    sitting(cfg, now - timedelta(minutes=5), minutes=30)
    assert not presence.at_desk(cfg, now, schedule.SETTLE)       # the camera stopped


@pytest.mark.parametrize("day", [MON, SAT], ids=["work day", "day off"])
def test_with_the_camera_a_checkin_waits_for_you_to_sit_down(cfg, day):
    now = at(10, day=day)
    sitting(cfg, now, label="away", minutes=30)
    assert schedule.what_now(cfg, now, TIMES) is None
    sitting(cfg, now)
    assert schedule.what_now(cfg, now, TIMES) == "morning"


def test_without_the_camera_work_days_keep_their_times_and_days_off_are_quiet(cfg):
    assert schedule.what_now(cfg, at(9, day=MON), TIMES) == "morning"
    assert schedule.what_now(cfg, at(9), TIMES) is None
    # A model that can't tell the empty desk can't tell you sat down either.
    sitting(cfg, at(9), sees_away=False)
    assert schedule.what_now(cfg, at(9), TIMES) is None
    sitting(cfg, at(9, day=MON), sees_away=False)
    assert schedule.what_now(cfg, at(9, day=MON), TIMES) == "morning"


def test_the_timer_runs_the_checkin_only_when_one_is_due(cfg, monkeypatch):
    runs = []
    monkeypatch.setattr(checkin, "run", lambda cfg, slot=None: runs.append(slot))
    monkeypatch.setattr(schedule, "what_now", lambda cfg, now, times: None)
    assert schedule.main() == 0 and runs == []
    monkeypatch.setattr(schedule, "what_now", lambda cfg, now, times: "midday")
    assert schedule.main() == 0 and runs == ["midday"]


def test_the_camera_runs_while_a_checkin_waits(cfg):
    assert camera.why_off(cfg, at(10), BLOCKS) == "outside focus blocks"          # a day off
    assert camera.why_off(cfg, at(10), BLOCKS, TIMES) is None                    # morning is due
    with st.locked_state(cfg, today=SAT.date()) as s:
        ran(s, "morning", at(9, 30))
    assert camera.why_off(cfg, at(10), BLOCKS, TIMES) == "outside focus blocks"  # had it
    assert camera.why_off(cfg, at(13), BLOCKS, TIMES) is None
    assert camera.why_off(cfg, at(21), BLOCKS, TIMES) == "outside focus blocks"
    evening = at(18, day=MON)                                                    # after the last block
    assert camera.why_off(cfg, evening, BLOCKS) == "outside focus blocks"
    assert camera.why_off(cfg, evening, BLOCKS, TIMES) is None


def test_status_says_what_the_next_checkin_waits_for(cfg):
    s = st.State(date=SAT.date().isoformat())
    assert schedule.waiting_line(cfg, s, at(8), TIMES) == "next one opens at 09:00"
    assert schedule.waiting_line(cfg, s, at(10), TIMES) == "none on a day off without the desk camera"
    assert schedule.waiting_line(cfg, s, at(10, day=MON), TIMES) == "morning due: it comes within 5 minutes"
    sitting(cfg, at(10))
    assert schedule.waiting_line(cfg, s, at(10), TIMES) == "morning due: it comes once you sit down at the desk"
    assert schedule.waiting_line(cfg, s, at(21), TIMES) == "none left today"


# --- days off: what the check-in is about ---------------------------------------------

def test_a_day_off_checkin_is_about_what_was_planned_for_it(cfg):
    fri, sat, sun = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
    with st.locked_state(cfg, today=fri) as s:
        actions.add_priority(s, "Finish the report")                       # work, left on Friday
        actions.add_priority(s, "Mow the lawn", sat)                        # planned for Saturday
        actions.add_priority(s, "Call mum", sun)
    with st.locked_state(cfg, today=sat) as s:
        bike = actions.add_priority(s, "Fix the bike")                     # added on Saturday
        bike.added_at = "2026-10-17T09:00:00-05:00"
        report = next(p for p in s.priorities if p.text == "Finish the report")
        report.done_at = "2026-10-17T09:30:00-05:00"                       # done anyway: acknowledged
        payload = coach.build_payload(s, "morning", "2026-10-17T10:00:00-05:00")
    assert [p["text"] for p in payload["priorities"]] == ["Finish the report", "Mow the lawn", "Fix the bike"]
    f = payload["facts"]
    assert (f["workday"], f["next_workday"], f["open_count"], f["done_count"]) == (False, "Monday", 2, 1)
    assert f["work_items_waiting"] == 0
    with st.locked_state(cfg, today=sun) as s:
        actions.add_priority(s, "Email Sam", date(2026, 10, 19))            # for Monday: not today
        s.find(bike.id).added_at = "2026-10-15T09:00:00-05:00"               # as if from Thursday
        payload = coach.build_payload(s, "evening", "2026-10-18T18:00:00-05:00")
    # Saturday's lawn still counts on Sunday; Thursday's bike doesn't.
    assert [p["text"] for p in payload["priorities"]] == ["Mow the lawn", "Call mum"]
    assert payload["facts"]["work_items_waiting"] == 1                    # the bike
    # Chat always sees the whole list.
    assert len(coach.build_payload(st.peek(cfg, today=sun), "chat", "2026-10-18T18:00:00-05:00")["priorities"]) == 3


def test_the_wrap_up_is_on_the_weeks_last_work_day(cfg, monkeypatch):
    monkeypatch.setenv("FOCUS_WORKDAYS", "mon-thu")
    thu = st.State(date="2026-10-15")
    assert coach.build_payload(thu, "evening", "2026-10-15T17:30:00-05:00").get("weekly_review") is True
    fri = st.State(date="2026-10-16")
    assert "weekly_review" not in coach.build_payload(fri, "evening", "2026-10-16T17:30:00-05:00")


def test_only_work_days_count_toward_carried_days(cfg, monkeypatch):
    sat, mon = date(2026, 10, 17), date(2026, 10, 19)
    with st.locked_state(cfg, today=sat) as s:
        p = actions.add_priority(s, "Write spec")
        p.added_at = "2026-10-17T08:00:00-05:00"
        ran(s, "morning", at(10))
    assert st.peek(cfg, today=mon).priorities[0].carried_days == 0
    monkeypatch.setenv("FOCUS_WORKDAYS", "mon-sat")                   # a Saturday at work counts
    assert st.peek(cfg, today=mon).priorities[0].carried_days == 1
