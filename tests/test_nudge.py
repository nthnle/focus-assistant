import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import completion, scripted
from focus import actions, blocks, config, feedback, nudge, state as st

BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)
TZ = timezone(timedelta(hours=-5))
NO_COACH = lambda cfg, now, minutes, n: None  # noqa: E731


def at(hour, minute, day=12):  # 2026-10-12 is a Monday
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def on_phone_since(cfg, when, *items):
    with st.locked_state(cfg, today=when.date()) as s:
        for text in items:
            actions.add_priority(s, text)
        actions.log_event(s, st.PHONE_OUT, at=when)


def events(cfg, day):
    return st.peek(cfg, today=day.date()).events


def test_due_every_five_minutes_on_the_phone():
    s = st.State(date="2026-10-12")
    assert nudge.due(s, at(10, 10), 5) is None                    # not on the phone
    actions.log_event(s, st.PHONE_OUT, at=at(10, 0))
    assert nudge.due(s, at(10, 4), 5) is None
    assert nudge.due(s, at(10, 5), 5) == (5, 1)
    actions.log_event(s, st.PHONE_NUDGE, "5 min", at=at(10, 5))
    assert nudge.due(s, at(10, 9), 5) is None                     # already said at 5
    assert nudge.due(s, at(10, 10), 5) == (10, 2)
    assert nudge.due(s, at(10, 10), 0) is None                    # turned off
    actions.log_event(s, st.PHONE_IN, "11 min", at=at(10, 11))
    assert nudge.due(s, at(10, 30), 5) is None                    # put down


def test_a_new_pickup_starts_the_count_again():
    s = st.State(date="2026-10-12")
    actions.log_event(s, st.PHONE_OUT, at=at(10, 0))
    actions.log_event(s, st.PHONE_NUDGE, "5 min", at=at(10, 5))
    actions.log_event(s, st.PHONE_IN, "6 min", at=at(10, 6))
    actions.log_event(s, st.PHONE_OUT, at=at(10, 20))
    assert nudge.due(s, at(10, 25), 5) == (5, 1)


def test_says_a_fixed_line_and_logs_it(cfg):
    on_phone_since(cfg, at(10, 0), "Write the spec")
    said = []
    assert nudge.run(cfg, at(10, 5), said.append, BLOCKS, write=NO_COACH)
    assert said == ["You've been on your phone for 5 minutes. Back to Write the spec."]
    assert events(cfg, at(10, 5))[-1] == {"at": "2026-10-12T10:05:00-05:00",
                                          "kind": st.PHONE_NUDGE, "detail": "5 min"}
    assert not nudge.run(cfg, at(10, 6), said.append, BLOCKS, write=NO_COACH)
    assert len(said) == 1


def test_fixed_line_with_an_empty_list(cfg):
    on_phone_since(cfg, at(10, 0))
    said = []
    nudge.run(cfg, at(10, 5), said.append, BLOCKS, write=NO_COACH)
    assert said == ["You've been on your phone for 5 minutes. Time to put it down."]


def test_uses_the_coach_line_and_keeps_its_cost(cfg):
    on_phone_since(cfg, at(10, 0))
    usage = {"model": "test-chat-model", "in": 400, "out": 15}
    said = []
    nudge.run(cfg, at(10, 5), said.append, BLOCKS,
              write=lambda c, now, minutes, n: {"text": "Five minutes on the phone. Back to the spec.",
                                                "usage": usage})
    assert said == ["Five minutes on the phone. Back to the spec."]
    assert events(cfg, at(10, 5))[-1]["usage"] == usage


@pytest.mark.parametrize("when", [at(12, 20), at(10, 20, day=17)], ids=["lunch", "saturday"])
def test_quiet_outside_focus_blocks(cfg, when):
    on_phone_since(cfg, when - timedelta(minutes=20))
    assert not nudge.run(cfg, when, [].append, BLOCKS, write=NO_COACH)


def test_quiet_while_paused(cfg):
    on_phone_since(cfg, at(10, 0))
    with st.locked_state(cfg, today=at(10, 0).date()) as s:
        actions.pause(s, at(11, 0))
    assert not nudge.run(cfg, at(10, 10), [].append, BLOCKS, write=NO_COACH)


def test_quiet_in_the_break_after_a_stretch_reminder(cfg):
    on_phone_since(cfg, at(10, 48))
    with st.locked_state(cfg, today=at(10, 0).date()) as s:
        actions.log_event(s, st.STRETCH_REMINDER, "fixed", at=at(10, 50))
    said = []
    assert not nudge.run(cfg, at(10, 55), said.append, BLOCKS, write=NO_COACH)
    assert nudge.run(cfg, at(11, 0), said.append, BLOCKS, write=NO_COACH)   # break's over
    assert said == ["You've been on your phone for 12 minutes. Time to put it down."]


def test_no_break_without_a_reminder_that_played(cfg):
    on_phone_since(cfg, at(10, 45))
    assert nudge.run(cfg, at(10, 55), [].append, BLOCKS, write=NO_COACH)


def test_compose_asks_the_chat_model_with_the_minutes(cfg):
    on_phone_since(cfg, at(10, 0), "Write the spec")
    client, sent = scripted(completion("Ten minutes now. Put it face down and open the spec.",
                                       model="test-chat-model", usage=(500, 14)))
    written = nudge.compose(cfg, at(10, 10), 10, 2, client=client)
    assert written["text"] == "Ten minutes now. Put it face down and open the spec."
    assert written["usage"]["in"] == 500 and written["usage"]["model"] == "test-chat-model"
    request = sent[0]
    assert request["model"] == "test-chat-model"
    payload = json.loads(request["messages"][-1]["content"])
    assert payload["minutes_on_phone"] == 10 and payload["nudge"] == 2
    assert payload["checkin"] == "phone_nudge"
    assert [p["text"] for p in payload["priorities"]] == ["Write the spec"]
    assert feedback.check("nudge", payload, written["text"]) == []


def test_compose_without_a_key_falls_back(cfg):
    on_phone_since(cfg, at(10, 0))
    assert nudge.compose(cfg, at(10, 5), 5, 1) is None
