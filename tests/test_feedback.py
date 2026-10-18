import json
from datetime import datetime, timedelta

import pytest

from conftest import completion, scripted, tool_call
from focus import actions, chat, checkin, cli, coach, config, feedback, state as st


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


MORNING = {"checkin": "morning", "facts": {"weekday": "Tuesday"},
           "priorities": [{"text": "Write the spec", "done": False}]}


@pytest.mark.parametrize("kind, payload, reply, problem", [
    ("checkin", MORNING, "Start with the spec before email.", None),
    ("checkin", MORNING, "Start with email today.", "doesn't name any open priority"),
    ("checkin", MORNING, "**Write the spec** first.", "markdown"),
    ("checkin", MORNING, "- Write the spec\n- then email", "markdown"),
    ("checkin", MORNING, "Write the spec first 🚀", "emoji"),
    ("checkin", MORNING, " ".join(["spec"] * 90), "over the 70-word limit"),
    ("checkin", {**MORNING, "weekly_review": True}, " ".join(["spec"] * 90), None),
    ("stretch", {}, " ".join(["up"] * 40), "over the 25-word limit"),
    ("checkin", {"checkin": "evening", "facts": {"weekday": "Sunday"}}, " ".join(["rest"] * 50), "weekend"),
    ("chat", {}, "  ", "empty"),
])
def test_check(kind, payload, reply, problem):
    found = feedback.check(kind, payload, reply)
    if problem is None:
        assert found == []
    else:
        assert any(problem in p for p in found), found


def test_record_rate_and_report(cfg):
    feedback.record(cfg, "checkin", MORNING, "Start with the spec.", "test-model")
    feedback.record(cfg, "checkin", MORNING, "Start with email.", "test-model")
    rated = feedback.rate(cfg, "bad", "it ignored my list")
    assert rated["reply"] == "Start with email."
    entries = feedback.entries(cfg)
    assert entries[0]["payload"] == MORNING and "rating" not in entries[0]
    assert entries[1]["rating"]["value"] == "bad"
    text = feedback.report(cfg)
    assert "2 replies, 1 to look at" in text
    assert "rated bad: it ignored my list" in text and "check: morning check-in" in text
    assert feedback.summary(cfg) == "2 in 14 days, 1 failed a check, 1 rated bad"
    with pytest.raises(ValueError):
        feedback.rate(cfg, "meh")


def test_old_entries_are_dropped(cfg):
    old = (datetime.now().astimezone() - timedelta(days=feedback.KEEP_DAYS + 1)).isoformat()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    feedback.path(cfg).write_text(json.dumps({"id": "old", "at": old, "kind": "chat", "reply": "x"}) + "\n")
    feedback.record(cfg, "chat", {}, "new", "m")
    assert [e["reply"] for e in feedback.entries(cfg)] == ["new"]


def test_check_ins_from_the_model_are_logged_and_offline_ones_are_not(cfg, monkeypatch):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Ship the parser")
    monkeypatch.setattr(coach, "ask", lambda c, payload, client=None:
                        coach.CoachReply("Start with the parser.", "test-model", 10, 5))
    checkin.run(cfg, "morning")
    [entry] = feedback.entries(cfg)
    assert entry["kind"] == "checkin" and entry["slot"] == "morning" and entry["checks"] == []
    assert entry["payload"]["priorities"][0]["text"] == "Ship the parser"

    def offline(*a, **k):
        raise coach.CoachError("down")
    monkeypatch.setattr(coach, "ask", offline)
    checkin.run(cfg, "midday")
    assert len(feedback.entries(cfg)) == 1


def test_telling_the_coach_it_was_wrong_rates_the_reply_before(cfg):
    feedback.record(cfg, "checkin", MORNING, "Nothing got done today.", "test-model")
    client, _ = scripted(
        completion(tool_calls=[tool_call("rate_reply", {"value": "bad", "note": "it's Sunday"})]),
        completion("Noted, sorry about that."),
    )
    chat.run(cfg, "that check-in was wrong, it's Sunday", client=client)
    first, second = feedback.entries(cfg)
    assert first["rating"] == {"value": "bad", "note": "it's Sunday", "at": first["rating"]["at"]}
    assert second["kind"] == "chat" and second["payload"]["message"] == "that check-in was wrong, it's Sunday"


def test_cli_rate_and_feedback(cfg, capsys):
    assert cli.main(["rate", "good"]) == 1          # nothing to rate yet
    feedback.record(cfg, "checkin", MORNING, "Start with email.", "test-model")
    cli.main(["rate", "bad", "wrong", "priority"])
    cli.main(["feedback"])
    out = capsys.readouterr().out
    assert "Rated bad: Start with email." in out and "rated bad: wrong priority" in out
