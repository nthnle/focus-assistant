import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import anthropic
import httpx2
import pytest

from focus import actions, chat, config, state as st
from focus.coach import CoachError
from focus.inputs import chat as chat_service

TOKEN = "t" * 24


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def scripted(*responses):
    """A client that returns the given messages in order and records each request body."""
    sent = []
    queue = list(responses)

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=queue.pop(0))

    client = anthropic.Anthropic(
        api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    return client, sent


def tool_use(name, args, id_="tu_1"):
    return {"type": "tool_use", "id": id_, "name": name, "input": args}


def text(t):
    return {"type": "text", "text": t}


def test_tool_call_changes_state_and_is_recorded(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
        actions.add_priority(s, "Email Sam")

    client, sent = scripted(
        _message([tool_use("complete_priority", {"id": 1})], stop_reason="tool_use"),
        _message([text("Marked the spec done. Email Sam next.")]),
    )
    reply = chat.run(cfg, "finished the spec", client=client)

    assert reply == "Marked the spec done. Email Sam next."
    with st.locked_state(cfg) as s:
        assert s.priorities[0].done
        assert s.chat[-1]["user"] == "finished the spec"
        assert s.chat[-1]["changed"] == ["complete_priority"]
        assert s.chat[-1]["tokens"] == {"in": 200, "out": 40}

    first = sent[0]
    assert first["model"] == "claude-haiku-5-5"   # chat uses FOCUS_CHAT_MODEL, not FOCUS_MODEL
    assert "thinking" not in first and "fallbacks" not in first
    assert first["output_config"] == {"effort": "low"}
    assert first["system"] == chat.SYSTEM_PROMPT
    assert {t["name"] for t in first["tools"]} == set(chat._HANDLERS)
    assert all(t["strict"] for t in first["tools"])
    assert "<data>" in first["messages"][-1]["content"]
    # Second call replays the assistant turn and sends the result
    result = sent[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert "[done] Write spec" in result["content"]


def test_sonnet_chat_runs_without_thinking(cfg, monkeypatch):
    monkeypatch.setenv("FOCUS_CHAT_MODEL", "claude-sonnet-5-5")
    client, sent = scripted(_message([text("Sure.")]))
    chat.run(config.load(), "hi", client=client)
    assert sent[0]["thinking"] == {"type": "between_tools"}
    assert sent[0]["fallbacks"] == "default"


def sse(*texts):
    """A streamed reply made of these text pieces, as server-sent events."""
    msg = _message([], stop_reason=None) | {"usage": {"input_tokens": 100, "output_tokens": 1}}
    events = [("message_start", {"type": "message_start", "message": msg}),
              ("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}})]
    events += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                        "delta": {"type": "text_delta", "text": t}}) for t in texts]
    events += [("content_block_stop", {"type": "content_block_stop", "index": 0}),
               ("message_delta", {"type": "message_delta",
                                  "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                  "usage": {"output_tokens": 12}}),
               ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def test_streaming_hands_over_text_as_it_arrives(cfg):
    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx2.Response(200, text=sse("Start with ", "the parser."),
                               headers={"content-type": "text/event-stream"})

    client = anthropic.Anthropic(api_key="test", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    pieces = []
    reply = chat.run(cfg, "what first?", client=client, on_text=pieces.append)
    assert pieces == ["Start with ", "the parser."]
    assert reply == "Start with the parser."
    with st.locked_state(cfg) as s:
        assert s.chat[-1]["tokens"] == {"in": 100, "out": 12}


def test_bad_tool_input_is_reported_to_the_model(cfg):
    client, sent = scripted(
        _message([tool_use("drop_priority", {"id": 99})], stop_reason="tool_use"),
        _message([text("I couldn't find that one.")]),
    )
    chat.run(cfg, "drop 99", client=client)
    result = sent[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "99" in result["content"]


def test_history_is_replayed_as_plain_text(cfg):
    client, _ = scripted(_message([text("Sure.")]))
    chat.run(cfg, "first", client=client)
    client, sent = scripted(_message([text("Again.")]))
    chat.run(cfg, "second", client=client)
    msgs = sent[0]["messages"]
    assert msgs[0] == {"role": "user", "content": "first"}
    assert msgs[1] == {"role": "assistant", "content": "Sure."}
    assert msgs[2]["content"].endswith("second")


def test_runaway_tool_loop_stops(cfg):
    looping = [_message([tool_use("report", {"how": "on_track"}, f"tu_{i}")], stop_reason="tool_use")
               for i in range(chat.MAX_STEPS)]
    client, _ = scripted(*looping)
    with pytest.raises(CoachError, match="too many steps"):
        chat.run(cfg, "hi", client=client)
    with st.locked_state(cfg) as s:
        assert s.chat == []


def test_daily_limit(cfg):
    client, _ = scripted(_message([text("ok")]))
    chat.run(cfg, "one", client=client, daily_limit=1)
    with pytest.raises(CoachError, match="limit"):
        chat.run(cfg, "two", client=client, daily_limit=1)


def test_chat_clears_at_rollover(cfg):
    from datetime import date
    with st.locked_state(cfg, today=date(2026, 10, 14)) as s:
        s.chat.append({"at": "x", "user": "hi", "reply": "hey"})
    with st.locked_state(cfg, today=date(2026, 10, 15)) as s:
        assert s.chat == []
        assert s.history[-1]["chats"] == 1


@pytest.fixture
def server(cfg):
    calls = []

    def fake_run(_cfg, text, daily_limit, on_text=None):
        calls.append(text)
        if text == "fail":
            raise CoachError("no network")
        return f"You said: {text}"

    handler = chat_service.make_handler(cfg, TOKEN, 50, run=fake_run)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", calls
    srv.shutdown()
    srv.server_close()


def post(url, body, token=TOKEN):
    req = urllib.request.Request(url, method="POST", data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def test_http_chat(server):
    base, calls = server
    assert post(f"{base}/chat", b'{"text": "hello"}') == (200, "You said: hello")
    assert post(f"{base}/chat", b'{"text": "fail"}') == (200, "Sorry, that didn't work: no network")
    assert post(f"{base}/chat", b'{"text": "x"}', token="wrong")[0] == 403
    assert post(f"{base}/chat", b'not json')[0] == 400
    assert post(f"{base}/other", b'{"text": "x"}')[0] == 404
    assert calls == ["hello", "fail"]
