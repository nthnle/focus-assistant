import json
from dataclasses import replace
from datetime import date

import pytest

from focus import actions, checkin, coach, config, state as st


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def test_new_state_is_created_for_today(cfg):
    with st.locked_state(cfg, today=date(2026, 10, 7)) as s:
        assert s.date == "2026-10-07"
        assert s.priorities == []
    assert json.loads(cfg.state_path.read_text())["date"] == "2026-10-07"


def test_rollover_carries_open_and_archives_done(cfg):
    with st.locked_state(cfg, today=date(2026, 10, 6)) as s:
        a = actions.add_priority(s, "Write spec")
        actions.add_priority(s, "Email Sam")
        actions.complete_priority(s, a.id)
        actions.report_drifted(s)
        s.checkins.append({"slot": "morning", "at": "x", "reply": "hi"})

    with st.locked_state(cfg, today=date(2026, 10, 7)) as s:
        assert [p.text for p in s.priorities] == ["Email Sam"]
        assert s.priorities[0].carried_days == 1
        assert s.checkins == [] and s.events == []
        day = s.history[-1]
        assert day["date"] == "2026-10-06"
        assert day["done"] == ["Write spec"]
        assert day["open"] == ["Email Sam"]
        assert day["events"] == {"self_report:drifted": 1}


def test_rollover_is_idempotent_and_ids_keep_increasing(cfg):
    with st.locked_state(cfg, today=date(2026, 10, 6)) as s:
        actions.add_priority(s, "One")
    with st.locked_state(cfg, today=date(2026, 10, 7)) as s:
        p = actions.add_priority(s, "Two")
    with st.locked_state(cfg, today=date(2026, 10, 7)) as s:
        assert p.id == 2
        assert s.priorities[0].carried_days == 1
        assert len(s.history) == 1


def test_history_is_trimmed(cfg):
    cfg = replace(cfg, history_days=2)
    for day in range(1, 6):
        with st.locked_state(cfg, today=date(2026, 10, day)):
            pass
    with st.locked_state(cfg, today=date(2026, 10, 6)) as s:
        assert [h["date"] for h in s.history] == ["2026-10-04", "2026-10-05"]


def test_failed_block_does_not_save(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Keep me")
    with pytest.raises(st.StateError):
        with st.locked_state(cfg) as s:
            actions.add_priority(s, "Lose me")
            actions.complete_priority(s, 99)
    with st.locked_state(cfg) as s:
        assert [p.text for p in s.priorities] == ["Keep me"]


def test_peek_reads_without_saving(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write the spec")
    before = cfg.state_path.stat().st_mtime_ns
    assert [p.text for p in st.peek(cfg).open_priorities] == ["Write the spec"]
    assert cfg.state_path.stat().st_mtime_ns == before


def test_move_and_drop(cfg):
    with st.locked_state(cfg) as s:
        for t in ("a", "b", "c"):
            actions.add_priority(s, t)
        actions.move_priority(s, 3, 1)
        assert [p.text for p in s.priorities] == ["c", "a", "b"]
        actions.drop_priority(s, 1)
        assert [p.text for p in s.priorities] == ["c", "b"]


def test_corrupt_state_raises_clearly(cfg):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_path.write_text("{not json")
    with pytest.raises(st.StateError, match="not valid state JSON"):
        with st.locked_state(cfg):
            pass


def test_slot_for_time_of_day():
    from datetime import datetime
    assert checkin.slot_for(datetime(2026, 10, 7, 9, 0)) == "morning"
    assert checkin.slot_for(datetime(2026, 10, 7, 13, 0)) == "midday"
    assert checkin.slot_for(datetime(2026, 10, 7, 17, 30)) == "evening"


def test_checkin_records_reply_and_prints(cfg, monkeypatch, capsys):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Ship the parser")

    seen = {}

    def fake_ask(_cfg, payload, client=None):
        seen.update(payload)
        return coach.CoachReply("Start with the parser.", "claude-opus-5-5", 100, 12)

    monkeypatch.setattr(coach, "ask", fake_ask)
    checkin.run(cfg, "morning")

    assert seen["checkin"] == "morning"
    assert seen["priorities"][0]["text"] == "Ship the parser"
    out = capsys.readouterr().out
    assert "Start with the parser." in out and "1. Ship the parser" in out
    with st.locked_state(cfg) as s:
        assert s.checkins[0]["reply"] == "Start with the parser."
        assert s.checkins[0]["tokens"] == {"in": 100, "out": 12}


def test_coach_failure_leaves_state_untouched(cfg, monkeypatch):
    def failing_ask(_cfg, payload, client=None):
        raise coach.CoachError("network down")

    monkeypatch.setattr(coach, "ask", failing_ask)
    with pytest.raises(coach.CoachError):
        checkin.run(cfg, "midday")
    with st.locked_state(cfg) as s:
        assert s.checkins == []


def test_last_prints_latest_reply(cfg, capsys):
    from focus import cli

    assert cli.main(["last"]) == 0
    assert "No check-in yet today" in capsys.readouterr().out

    with st.locked_state(cfg) as s:
        s.checkins.append({"slot": "morning", "at": "2026-10-12T09:00:04-05:00", "reply": "Old."})
        s.checkins.append({"slot": "midday", "at": "2026-10-12T13:00:02-05:00", "reply": "Dock the phone."})
    assert cli.main(["last"]) == 0
    out = capsys.readouterr().out
    assert out == "[midday check-in, 13:00]\nDock the phone.\n"
