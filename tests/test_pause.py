from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import completion, scripted, tool_call
from focus import actions, chat, checkin, cli, coach, config, pause, state as st, stretch, blocks
from focus.inputs import voice
from focus.outputs import speech

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 12, 13, 50, tzinfo=TZ)  # a Monday afternoon


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


class FakeVoice:
    def synthesize_wav(self, text, wav):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0")


@pytest.mark.parametrize("text, expected", [
    ("15:00", datetime(2026, 10, 12, 15, 0, tzinfo=TZ)),
    ("until 3pm", datetime(2026, 10, 12, 15, 0, tzinfo=TZ)),
    ("2 PM", datetime(2026, 10, 12, 14, 0, tzinfo=TZ)),
    ("45m", NOW + timedelta(minutes=45)),
    ("2 hours", NOW + timedelta(hours=2)),
    ("tomorrow", datetime(2026, 10, 13, 0, 0, tzinfo=TZ)),
    ("fri", datetime(2026, 10, 16, 0, 0, tzinfo=TZ)),
    ("Monday", datetime(2026, 10, 19, 0, 0, tzinfo=TZ)),   # today is Monday: next week's
    ("2026-10-20", datetime(2026, 10, 20, 0, 0, tzinfo=TZ)),
])
def test_parse_until(text, expected):
    assert pause.parse_until(text, NOW) == expected


@pytest.mark.parametrize("text", ["13:00", "soon", "25:00", "13pm", "2026-10-01", "2000h", ""])
def test_parse_until_rejects_what_it_cannot_use(text):
    with pytest.raises(ValueError):
        pause.parse_until(text, NOW)


def test_a_short_pause_is_not_a_day_off():
    s = st.State(date=NOW.date().isoformat(), paused_until=(NOW + timedelta(hours=1)).isoformat())
    assert pause.paused(s, NOW) and not pause.day_off(s, NOW)
    assert pause.describe(s, NOW) == "until 14:50"
    assert not pause.paused(s, NOW + timedelta(hours=2))
    s.paused_until = datetime(2026, 10, 19, tzinfo=TZ).isoformat()
    assert pause.day_off(s, NOW) and pause.describe(s, NOW) == "until Mon 19 Oct"


def test_check_in_is_skipped_on_a_day_off(cfg, monkeypatch):
    monkeypatch.setattr(coach, "ask", lambda *a, **k: pytest.fail("no model call on a day off"))
    with st.locked_state(cfg) as s:
        actions.pause(s, pause.parse_until("tomorrow", datetime.now().astimezone()))
    assert checkin.run(cfg, "morning") is None
    with st.locked_state(cfg) as s:
        assert s.checkins == []


def test_check_in_during_a_short_pause_runs_but_is_held(cfg, monkeypatch):
    monkeypatch.setattr(speech, "datetime", type("D", (), {"now": staticmethod(lambda: NOW)}))
    s = st.State(date=NOW.date().isoformat(), paused_until=(NOW + timedelta(minutes=30)).isoformat())
    s.checkins.append({"slot": "midday", "at": NOW.isoformat(), "reply": "Back to the report."})
    played = []
    speech.Speech(FakeVoice(), played.append, cfg).checkin(s, "midday", "Back to the report.")
    assert played == [] and s.checkins[-1]["unheard"] is True


def test_held_check_in_is_said_when_the_pause_ends(cfg):
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.paused_until = (NOW + timedelta(minutes=10)).isoformat()
        s.checkins.append({"slot": "midday", "at": NOW.isoformat(), "reply": "Back to it.", "unheard": True})
    said = []
    assert not voice.replay_missed(cfg, said.append, NOW)
    assert voice.replay_missed(cfg, said.append, NOW + timedelta(minutes=11))
    assert said == ["You missed the midday check-in. It said: Back to it."]


def test_stretch_reminder_skipped_while_paused(cfg):
    local = datetime(2026, 10, 12, 13, 50)  # naive, like the timer's datetime.now()
    with st.locked_state(cfg, today=local.date()) as s:
        s.paused_until = (local.astimezone() + timedelta(minutes=30)).isoformat()
    said = []
    assert not stretch.run(cfg, local, said.append, blocks.parse_blocks(blocks.DEFAULT_BLOCKS),
                           write=lambda c, n: None)
    assert said == []


def test_pause_survives_rollover(cfg):
    with st.locked_state(cfg, today=date(2026, 10, 9)) as s:
        s.paused_until = "2026-10-12T00:00:00-05:00"
    with st.locked_state(cfg, today=date(2026, 10, 10)) as s:
        assert s.paused_until == "2026-10-12T00:00:00-05:00"


def _day(cfg, day, *, checkin_at=None, add=None, added_at=None):
    with st.locked_state(cfg, today=day) as s:
        if add:
            p = actions.add_priority(s, add)
            p.added_at = added_at
        if checkin_at:
            s.checkins.append({"slot": "evening", "at": checkin_at, "reply": "ok"})
        return s


def test_carried_days_count_only_check_in_days_and_not_evening_plans(cfg):
    _day(cfg, date(2026, 10, 12), add="Write spec", added_at="2026-10-12T08:00:00-05:00",
         checkin_at="2026-10-12T17:30:00-05:00")
    _day(cfg, date(2026, 10, 12), add="Book dentist", added_at="2026-10-12T20:00:00-05:00")
    s = _day(cfg, date(2026, 10, 13))
    assert {p.text: p.carried_days for p in s.priorities} == {"Write spec": 1, "Book dentist": 0}
    # The 13th had no check-in (a day off): nothing counts.
    s = _day(cfg, date(2026, 10, 14))
    assert {p.text: p.carried_days for p in s.priorities} == {"Write spec": 1, "Book dentist": 0}


def test_cli_numbers_by_position(cfg, capsys):
    for text in ("Write the spec", "Email Sam", "Review PR", "Book dentist"):
        cli.main(["add", text])
    capsys.readouterr()
    cli.main(["done", "1"])
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "[x] 1. Write the spec",
        "[ ] 2. Email Sam",
        "[ ] 3. Review PR",
        "[ ] 4. Book dentist",
    ]
    cli.main(["edit", "4", "Book", "the", "dentist"])
    assert "[ ] 4. Book the dentist" in capsys.readouterr().out
    assert cli.main(["drop", "9"]) == 1
    assert "No item 9" in capsys.readouterr().err


def test_cli_pause_and_resume(cfg, capsys):
    cli.main(["pause", "tomorrow"])
    assert "Paused until" in capsys.readouterr().out
    with st.locked_state(cfg) as s:
        assert s.paused_until and s.events[-1]["kind"] == st.PAUSE
    cli.main(["resume"])
    with st.locked_state(cfg) as s:
        assert s.paused_until is None and s.events[-1]["kind"] == st.RESUME
    assert cli.main(["pause", "soon"]) == 1


def test_chat_can_pause_resume_and_rename(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Wrte spec")
    client, sent = scripted(
        completion(tool_calls=[tool_call("rename_priority", {"id": 1, "text": "Write spec"}),
                               tool_call("pause", {"until": "tomorrow"}, id_="call_2")]),
        completion("Fixed, and quiet until tomorrow."),
    )
    chat.run(cfg, "fix the typo and I'm off for the rest of the day", client=client)
    with st.locked_state(cfg) as s:
        assert s.priorities[0].text == "Write spec"
        assert s.paused_until is not None
    assert "Paused until" in sent[1]["messages"][-1]["content"]

    client, _ = scripted(completion(tool_calls=[tool_call("resume", {})]), completion("Back on."))
    chat.run(cfg, "actually I'm back", client=client)
    with st.locked_state(cfg) as s:
        assert s.paused_until is None
