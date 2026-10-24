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
        for p in s.priorities:
            p.added_at = "2026-10-06T08:00:00+00:00"
        actions.complete_priority(s, a.id)
        actions.report_drifted(s)
        s.checkins.append({"slot": "morning", "at": "2026-10-06T09:00:00+00:00", "reply": "hi"})

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
        assert s.priorities[0].carried_days == 0  # no check-in ran on the 6th
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


def test_drop(cfg):
    with st.locked_state(cfg) as s:
        for t in ("a", "b", "c"):
            actions.add_priority(s, t)
        actions.drop_priority(s, 1)
        assert [p.text for p in s.priorities] == ["b", "c"]


def test_suggested_is_the_check_ins_pick_while_it_is_open(cfg):
    with st.locked_state(cfg) as s:
        for t in ("a", "b", "c"):
            actions.add_priority(s, t)
        assert s.suggested is None   # no check-in has picked one
        s.checkins.append({"slot": "morning", "at": "x", "reply": "Start with b.", "start_id": 2})
        s.checkins.append({"slot": "midday", "at": "y", "reply": "Keep going."})   # picked none
        assert s.suggested.text == "b"
        actions.complete_priority(s, 2)
        assert s.suggested is None   # done; "a" and "c" are both open, so no guess
        actions.complete_priority(s, 1)
        assert s.suggested.text == "c"   # the only one left


def test_checkin_saves_the_item_it_suggested(cfg, monkeypatch):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Ship the parser")
    monkeypatch.setattr(coach, "ask", lambda _cfg, payload, client=None:
                        coach.CoachReply("Start with the parser.", "test-model", 1, 1, start_id=1))
    checkin.run(cfg, "morning")
    with st.locked_state(cfg) as s:
        assert s.checkins[-1]["start_id"] == 1
        assert s.checkins[-1]["reply"] == "Start with the parser."


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
        return coach.CoachReply("Start with the parser.", "test-model", 100, 12)

    monkeypatch.setattr(coach, "ask", fake_ask)
    checkin.run(cfg, "morning")

    assert seen["checkin"] == "morning"
    assert seen["priorities"][0]["text"] == "Ship the parser"
    out = capsys.readouterr().out
    assert "Start with the parser." in out and "1. Ship the parser" in out
    with st.locked_state(cfg) as s:
        assert s.checkins[0]["reply"] == "Start with the parser."
        assert s.checkins[0]["tokens"] == {"in": 100, "out": 12}


def test_coach_failure_gives_the_offline_check_in(cfg, monkeypatch, capsys):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Ship the parser")

    def failing_ask(_cfg, payload, client=None):
        raise coach.CoachError("The coach is offline: can't reach the model provider")

    monkeypatch.setattr(coach, "ask", failing_ask)
    text = checkin.run(cfg, "midday")

    assert text == "The coach is offline. 1 item open on today's list."
    assert text in capsys.readouterr().out
    with st.locked_state(cfg) as s:
        assert s.checkins[-1]["reply"] == text
        assert s.checkins[-1]["model"] == "offline"
        assert "tokens" not in s.checkins[-1]


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


def test_checkin_saves_its_cost_when_the_price_is_known(cfg, monkeypatch):
    monkeypatch.setenv("FOCUS_MODEL_PRICE", "0.15,0.60")
    monkeypatch.setattr(coach, "ask", lambda _cfg, payload, client=None:
                        coach.CoachReply("Go.", "test-model", 1000, 100))
    checkin.run(cfg, "morning")
    with st.locked_state(cfg) as s:
        assert s.checkins[-1]["usd"] == pytest.approx((1000 * 0.15 + 100 * 0.60) / 1e6)


def test_write_json_leaves_the_old_file_and_no_temp_file_on_failure(tmp_path):
    path = tmp_path / "camera.json"
    st.write_json(path, {"seen": "work"}, durable=False)
    with pytest.raises(TypeError):
        st.write_json(path, {"seen": object()})          # not JSON
    assert json.loads(path.read_text()) == {"seen": "work"}
    assert [p.name for p in tmp_path.iterdir()] == ["camera.json"]


def test_onnxruntime_telemetry_is_turned_off_when_it_is_there(monkeypatch):
    import sys
    import types
    from focus import onnx
    calls = []
    monkeypatch.setitem(sys.modules, "onnxruntime",
                        types.SimpleNamespace(disable_telemetry_events=lambda: calls.append(1)))
    onnx.no_telemetry()
    assert calls == [1]
    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace())   # an older version
    onnx.no_telemetry()
    monkeypatch.setitem(sys.modules, "onnxruntime", None)                     # not installed
    onnx.no_telemetry()
