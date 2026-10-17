import json

import httpx
import pytest
from conftest import client_for, completion, scripted

from focus import coach, config
from focus.state import Priority, State


def test_request_shape_and_reply():
    client, sent = scripted(completion("Start with the parser.", usage=(50, 9)))
    reply = coach.ask(config.load(), {"checkin": "morning"}, client=client)

    assert reply.text == "Start with the parser."
    assert (reply.input_tokens, reply.output_tokens) == (50, 9)
    body = sent[0]
    assert body["model"] == "test-model"
    assert body["messages"][0] == {"role": "system", "content": coach.SYSTEM_PROMPT}
    assert json.loads(body["messages"][1]["content"]) == {"checkin": "morning"}
    assert "tools" not in body
    assert "reasoning_effort" not in body  # most open models reject it


def test_effort_is_sent_only_when_set(monkeypatch):
    monkeypatch.setenv("FOCUS_EFFORT", "low")
    client, sent = scripted(completion("Go."))
    coach.ask(config.load(), {}, client=client)
    assert sent[0]["reasoning_effort"] == "low"


def test_thinking_is_stripped_from_the_reply():
    client, _ = scripted(completion("<think>They have two tasks...</think>\n\nStart with the parser."))
    assert coach.ask(config.load(), {}, client=client).text == "Start with the parser."


def test_think_filter_handles_tags_split_across_pieces():
    f = coach.ThinkFilter()
    pieces = ["Hi <thi", "nk>hidden</th", "ink> there", ", 3 < 4"]
    assert "".join(f.feed(p) for p in pieces) + f.flush() == "Hi  there, 3 < 4"


def test_empty_reply_is_an_error():
    client, _ = scripted(completion("<think>only thoughts</think>", finish_reason="length"))
    with pytest.raises(coach.CoachError, match="length"):
        coach.ask(config.load(), {}, client=client)


def test_auth_failure_points_at_env_file():
    client = client_for(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(coach.CoachError, match="FOCUS_LLM_API_KEY"):
        coach.ask(config.load(), {}, client=client)


def test_unreachable_provider_says_offline():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(coach.CoachError, match="offline"):
        coach.ask(config.load(), {}, client=client_for(handler))


def test_missing_provider_settings_are_explained():
    with pytest.raises(coach.CoachError, match="systemctl start focus-checkin"):
        coach.ask(config.load(), {})


def test_missing_model_is_explained(monkeypatch):
    monkeypatch.delenv("FOCUS_MODEL")
    client, _ = scripted()
    with pytest.raises(coach.CoachError, match="FOCUS_MODEL"):
        coach.ask(config.load(), {}, client=client)


@pytest.mark.parametrize("slot, open_, expected", [
    ("morning", ["Ship the parser"], "Start with: Ship the parser."),
    ("morning", [], "Set up to three priorities"),
    ("midday", ["Ship the parser"], "Still open at the top: Ship the parser."),
    ("evening", ["Email Sam"], "1 done today; first thing tomorrow: Email Sam."),
])
def test_offline_reply_uses_the_data(slot, open_, expected):
    s = State(date="2026-10-12", priorities=[Priority(1, "Write spec", done_at="2026-10-12T10:00:00")]
              + [Priority(i, t) for i, t in enumerate(open_, 2)])
    reply = coach.offline_reply(s, slot)
    assert reply.startswith("The coach is offline.")
    assert expected in reply


def _week_state(today):
    days = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]  # last Fri, Mon-Thu
    return State(date=today, history=[{"date": d, "done": [d], "open": [], "checkins": 3,
                                       "chats": 0, "events": {}} for d in days])


def test_friday_evening_is_the_weekly_wrap_up():
    payload = coach.build_payload(_week_state("2026-10-16"), "evening", "2026-10-16T17:30:00-05:00")
    assert payload["weekly_review"] is True
    assert [h["date"] for h in payload["this_week"]] == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    assert "Friday evening (weekly_review is true)" in coach.SYSTEM_PROMPT


@pytest.mark.parametrize("today, slot", [("2026-10-16", "midday"), ("2026-10-15", "evening")],
                         ids=["friday midday", "thursday evening"])
def test_other_check_ins_are_not_weekly(today, slot):
    payload = coach.build_payload(_week_state(today), slot, f"{today}T17:30:00-05:00")
    assert "weekly_review" not in payload and "this_week" not in payload
