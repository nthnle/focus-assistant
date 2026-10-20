import json
from datetime import datetime

import httpx
import pytest

from conftest import client_for, completion, scripted
from focus import actions, blocks, config, state as st, stretch

BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)
NO_COACH = lambda cfg, now: None  # noqa: E731


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def at(hour, minute=50, day=12):  # 2026-10-12 is a Monday
    return datetime(2026, 10, day, hour, minute)


def test_speaks_and_logs_during_a_block(cfg):
    said = []
    assert stretch.run(cfg, at(10), said.append, BLOCKS, write=NO_COACH)
    assert said == [stretch.phrase_for(at(10))]
    with st.locked_state(cfg, today=at(10).date()) as s:
        assert [e["kind"] for e in s.events] == [stretch.STRETCH]


def test_uses_the_coach_line_when_there_is_one(cfg):
    said = []
    usage = {"model": "test-chat-model", "in": 300, "out": 12}
    stretch.run(cfg, at(10), said.append, BLOCKS,
                write=lambda c, n: {"text": "Up you get, the spec can wait.", "usage": usage})
    assert said == ["Up you get, the spec can wait."]
    with st.locked_state(cfg, today=at(10).date()) as s:
        event = s.events[-1]
    assert event["detail"] == "coach" and event["usage"] == usage


@pytest.mark.parametrize("when", [at(12), at(17), at(8), at(10, day=17)],
                         ids=["lunch", "after the last block", "before work", "saturday"])
def test_quiet_outside_blocks(cfg, when):
    said, asked = [], []
    assert not stretch.run(cfg, when, said.append, BLOCKS, write=lambda c, n: asked.append(n))
    assert said == [] and asked == []  # doesn't even ask the coach


def test_timer_slots_that_speak():
    # The timer fires at :50 from 09 to 17; these are the ones inside the default blocks.
    speaking = [h for h in range(9, 18) if blocks.in_block(at(h), BLOCKS)]
    assert speaking == [9, 10, 11, 13, 14, 15, 16]


def test_consecutive_fixed_phrases_differ():
    phrases = [stretch.phrase_for(at(h)) for h in range(9, 18)]
    assert all(a != b for a, b in zip(phrases, phrases[1:]))


def test_force_speaks_anytime(cfg):
    said = []
    assert stretch.run(cfg, at(20), said.append, BLOCKS, force=True, write=NO_COACH)
    assert len(said) == 1


def test_compose_asks_the_chat_model_with_the_days_data(cfg):
    with st.locked_state(cfg, today=at(10).date()) as s:
        actions.add_priority(s, "Write spec")
    client, sent = scripted(completion("Stand up and stretch before more spec.",
                                       model="test-chat-model", usage=(300, 12)))

    assert stretch.compose(cfg, at(10), client=client) == {
        "text": "Stand up and stretch before more spec.",
        "usage": {"model": "test-chat-model", "in": 300, "out": 12},
    }
    assert sent[0]["model"] == "test-chat-model"
    assert sent[0]["messages"][0] == {"role": "system", "content": stretch.SYSTEM_PROMPT}
    payload = json.loads(sent[0]["messages"][1]["content"])
    assert payload["checkin"] == "stretch_break"
    assert payload["priorities"][0]["text"] == "Write spec"


def test_compose_falls_back_when_the_coach_fails(cfg):
    busy = client_for(lambda r: httpx.Response(503, json={"error": {"message": "busy"}}))
    assert stretch.compose(cfg, at(10), client=busy) is None
    said = []
    stretch.run(cfg, at(10), said.append, BLOCKS,
                write=lambda c, n: stretch.compose(c, n, client=busy))
    assert said == [stretch.phrase_for(at(10))]


def test_compose_without_a_key_falls_back(cfg, monkeypatch):
    monkeypatch.delenv("FOCUS_LLM_API_KEY", raising=False)
    assert stretch.compose(cfg, at(10)) is None


def reminded_at(cfg, when):
    stretch.run(cfg, when, lambda text: None, BLOCKS, write=NO_COACH)


def test_back_to_work_after_a_break(cfg):
    said = []
    with st.locked_state(cfg, today=at(10).date()) as s:
        actions.add_priority(s, "Write the spec")
        actions.add_priority(s, "Email Sam")
    reminded_at(cfg, at(10))
    assert stretch.back(cfg, at(11, 0), said.append, BLOCKS)
    assert said == ["Break's over. Back to it. 2 things on your list."]


def test_no_back_to_work_without_a_reminder_at_50(cfg):
    said = []
    assert not stretch.back(cfg, at(11, 0), said.append, BLOCKS)       # none played
    reminded_at(cfg, at(9))
    assert not stretch.back(cfg, at(11, 0), said.append, BLOCKS)       # too long ago
    assert said == []


def test_no_back_to_work_at_lunch(cfg):
    said = []
    reminded_at(cfg, at(11))                                          # 11:50 in the block
    assert not stretch.back(cfg, at(12, 0), said.append, BLOCKS)       # 12:00 is lunch
    assert said == []


def test_no_back_to_work_while_paused(cfg):
    said = []
    reminded_at(cfg, at(10))
    with st.locked_state(cfg, today=at(10).date()) as s:
        actions.pause(s, at(12, 0).astimezone())
    assert not stretch.back(cfg, at(11, 0), said.append, BLOCKS)
    assert said == []


def test_back_line_counts_what_is_left():
    s = st.State(date="2026-10-12")
    assert stretch.back_line(s).endswith("Your list is clear.")
    actions.add_priority(s, "Write the spec")
    assert stretch.back_line(s).endswith("One thing on your list.")
