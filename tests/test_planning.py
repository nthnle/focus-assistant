import json
from datetime import date

import pytest

from conftest import completion, scripted, tool_call
from focus import actions, chat, checkin, cli, coach, config, state as st

MON, TUE, WED, FRI, NEXT_MON = (date(2026, 10, d) for d in (12, 13, 14, 16, 19))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def day(cfg, when):
    return st.locked_state(cfg, today=when)


def checked_in(s, at="17:30"):
    s.checkins.append({"slot": "evening", "at": f"{s.date}T{at}:00-05:00", "reply": "ok"})


# --- later ---------------------------------------------------------------

def test_a_later_priority_waits_off_the_list_and_never_counts_as_carried(cfg):
    with day(cfg, MON) as s:
        actions.add_priority(s, "Write spec").added_at = "2026-10-12T08:00:00-05:00"
        actions.add_priority(s, "Book dentist", WED)
        actions.add_priority(s, "Learn Rust", st.LATER)
        assert [p.text for p in s.open_priorities] == ["Write spec"]
        assert [p.text for p in s.later] == ["Book dentist", "Learn Rust"]
        checked_in(s)
    with day(cfg, TUE) as s:
        checked_in(s)
        assert [p.text for p in s.open_priorities] == ["Write spec"]
    with day(cfg, WED) as s:
        assert [(p.text, p.carried_days) for p in s.open_priorities] == [("Write spec", 2), ("Book dentist", 0)]
        assert [p.text for p in s.later] == ["Learn Rust"]


def test_scheduling_rules(cfg):
    with day(cfg, MON) as s:
        p = actions.add_priority(s, "Write spec")
        with pytest.raises(st.StateError):
            actions.schedule_priority(s, p.id, MON)       # not a later day
        actions.schedule_priority(s, p.id, st.LATER)
        assert s.open_priorities == []
        actions.schedule_priority(s, p.id, None)
        assert s.open_priorities == [p] and p.on is None


def test_numbers_follow_the_shown_order(cfg, capsys):
    for args in (["add", "Write spec"], ["add", "--on", "later", "Learn Rust"], ["add", "Email Sam"]):
        cli.main(args)
    capsys.readouterr()
    cli.main(["show"])
    out = capsys.readouterr().out.splitlines()
    assert out == ["[ ] 1. Write spec", "[ ] 2. Email Sam", "Later:", "[ ] 3. Learn Rust  (someday)"]
    cli.main(["now", "3"])
    assert "[ ] 2. Learn Rust" in capsys.readouterr().out   # back in the order it was added
    with pytest.raises(SystemExit):   # no ranking, so no move command
        cli.main(["move", "2", "1"])
    assert cli.main(["add", "--on", "someday-ish", "X"]) == 1


# --- the week's handover ----------------------------------------------------

def test_fridays_review_reaches_monday(cfg):
    with day(cfg, FRI) as s:
        checked_in(s)
        payload = coach.build_payload(s, "evening", "2026-10-16T17:30:00-05:00")
        assert payload["weekly_review"]
        assert payload["routine"]["focus_blocks"] == ["09:00-12:00", "13:00-17:30"]
        checkin._remember_the_week(s, payload, "Good week. Next week, try a break at 15:00.")
    with day(cfg, NEXT_MON) as s:
        assert set(s.last_week) == {"week", "review", "opened"}
        payload = coach.build_payload(s, "morning", "2026-10-19T09:00:00-05:00")
        assert payload["week_start"]
        assert payload["last_week"]["review"] == "Good week. Next week, try a break at 15:00."
        assert "weekly_review" not in payload
        checkin._remember_the_week(s, payload, "New week.")
        assert s.last_week["opened"]
        assert "week_start" not in coach.build_payload(s, "morning", "2026-10-19T09:30:00-05:00")


def test_a_missed_friday_review_happens_on_monday_morning(cfg):
    with day(cfg, TUE) as s:
        checked_in(s)
    with day(cfg, FRI):
        pass  # a day off: no check-in, no review
    with day(cfg, NEXT_MON) as s:
        payload = coach.build_payload(s, "morning", "2026-10-19T09:00:00-05:00")
        assert payload["weekly_review"] and payload["late"]
        assert [h["date"] for h in payload["this_week"]] == ["2026-10-13", "2026-10-16"]
        checkin._remember_the_week(s, payload, "Last week, briefly.")
        assert s.last_week["review"] == "Last week, briefly."


# --- routine patterns -------------------------------------------------------

def test_history_keeps_the_patterns_for_routine_suggestions(cfg):
    with day(cfg, MON) as s:
        p = actions.add_priority(s, "Write spec")
        p.done_at = "2026-10-12T10:42:00-05:00"
        s.events = [
            {"at": "2026-10-12T14:05:00-05:00", "kind": st.PHONE_OUT},
            {"at": "2026-10-12T14:20:00-05:00", "kind": st.PHONE_IN},
            {"at": "2026-10-12T14:40:00-05:00", "kind": st.PHONE_OUT},
            {"at": "2026-10-12T14:45:00-05:00", "kind": st.PHONE_IN},
        ]
        checked_in(s, "09:00")
    with day(cfg, TUE) as s:
        h = s.history[-1]
    assert h["checkin_times"] == ["09:00"] and h["done_times"] == ["10:42"]
    assert h["phone_pickups"] == 2 and h["phone_minutes"] == 20
    assert h["pickups_by_hour"] == {"14": 2}


def test_chat_gets_routine_settings_and_can_plan(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Book dentist")
    client, sent = scripted(
        completion(tool_calls=[tool_call("schedule_priority", {"id": 1, "on": "later"})]),
        completion("Moved the dentist to later."),
    )
    chat.run(cfg, "the dentist can wait", client=client)
    assert "routine" in sent[0]["messages"][-1]["content"]
    assert "[on later] Book dentist" in sent[1]["messages"][-1]["content"]
    with st.locked_state(cfg) as s:
        assert s.open_priorities == []


# --- weekly goals are gone ---------------------------------------------------

SUN = date(2026, 10, 18)


def test_a_state_file_with_weekly_goals_still_loads(cfg):
    """Files saved while there were weekly goals: the goals are dropped quietly."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_path.write_text(json.dumps({
        "version": st.SCHEMA_VERSION, "date": "2026-10-18",
        "goals": [{"id": 1, "text": "GEIR", "done_at": None, "week": "2026-10-19"}],
        "last_week": {"week": "2026-10-05", "goals": [{"id": 1, "text": "Old"}], "review": "Try 15:00.", "opened": True},
    }))
    with day(cfg, SUN) as s:
        assert "goals" not in s.to_dict()
        assert s.last_week == {"week": "2026-10-05", "review": "Try 15:00.", "opened": True}
        payload = coach.build_payload(s, "evening", "2026-10-18T16:20:00-05:00")
        assert payload["facts"]["weekday"] == "Sunday"
        assert not any("goal" in key for key in payload)


def test_there_are_no_goal_commands_or_tools(cfg):
    assert not any("goal" in t["function"]["name"] for t in chat.TOOLS)
    with pytest.raises(SystemExit):
        cli.main(["goal", "add", "Ship the beta"])
