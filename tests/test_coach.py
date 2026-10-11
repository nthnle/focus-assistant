import json

import anthropic
import httpx2
import pytest

from focus import coach, config


def _client(handler):
    return anthropic.Anthropic(
        api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 9},
    }


def test_request_shape_and_reply():
    sent = {}

    def handler(request):
        sent["beta"] = request.headers.get("anthropic-beta")
        sent["body"] = json.loads(request.content)
        return httpx2.Response(200, json=_message([{"type": "text", "text": "Start with the parser."}]))

    reply = coach.ask(config.load(), {"checkin": "morning"}, client=_client(handler))

    assert reply.text == "Start with the parser."
    assert (reply.input_tokens, reply.output_tokens) == (50, 9)
    assert sent["beta"] == "server-side-fallback-2026-07-01"
    body = sent["body"]
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"}
    assert body["system"] == coach.SYSTEM_PROMPT
    assert json.loads(body["messages"][0]["content"]) == {"checkin": "morning"}


def test_refusal_is_an_error():
    handler = lambda r: httpx2.Response(200, json=_message([], stop_reason="refusal"))
    with pytest.raises(coach.CoachError, match="declined"):
        coach.ask(config.load(), {}, client=_client(handler))


def test_auth_failure_points_at_env_file():
    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})

    with pytest.raises(coach.CoachError, match="focus.env"):
        coach.ask(config.load(), {}, client=_client(handler))


def test_missing_key_is_explained(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(coach.CoachError, match="systemctl start focus-checkin"):
        coach.ask(config.load(), {})
