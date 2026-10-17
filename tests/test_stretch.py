import json
from datetime import datetime

import anthropic
import httpx2
import pytest

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
    stretch.run(cfg, at(10), said.append, BLOCKS, write=lambda c, n: "Up you get, the spec can wait.")
    assert said == ["Up you get, the spec can wait."]


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


def _client(handler):
    return anthropic.Anthropic(api_key="test", max_retries=0,
                               http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_compose_asks_the_chat_model_with_the_days_data(cfg):
    with st.locked_state(cfg, today=at(10).date()) as s:
        actions.add_priority(s, "Write spec")
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": "Stand up and stretch before more spec."}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 300, "output_tokens": 12}})

    assert stretch.compose(cfg, at(10), client=_client(handler)) == "Stand up and stretch before more spec."
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["system"] == stretch.SYSTEM_PROMPT
    payload = json.loads(sent["messages"][0]["content"])
    assert payload["checkin"] == "stretch_break"
    assert payload["priorities"][0]["text"] == "Write spec"


def test_compose_falls_back_when_the_coach_fails(cfg):
    handler = lambda r: httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    assert stretch.compose(cfg, at(10), client=_client(handler)) is None
    said = []
    stretch.run(cfg, at(10), said.append, BLOCKS,
                write=lambda c, n: stretch.compose(c, n, client=_client(handler)))
    assert said == [stretch.phrase_for(at(10))]


def test_compose_without_a_key_falls_back(cfg, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert stretch.compose(cfg, at(10)) is None
