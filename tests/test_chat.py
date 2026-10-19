import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import httpx
import pytest
from conftest import client_for, completion, scripted, sse, tool_call

from focus import actions, chat, config, state as st
from focus.coach import CoachError
from focus.inputs import chat as chat_service

TOKEN = "t" * 24


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def test_tool_call_changes_state_and_is_recorded(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
        actions.add_priority(s, "Email Sam")

    client, sent = scripted(
        completion(tool_calls=[tool_call("complete_priority", {"id": 1})]),
        completion("Marked the spec done. Email Sam next."),
    )
    reply = chat.run(cfg, "finished the spec", client=client)

    # The top moved, so the code names what is next.
    assert reply == "Marked the spec done. Email Sam next. Next is Email Sam."
    with st.locked_state(cfg) as s:
        assert s.priorities[0].done
        assert s.chat[-1]["user"] == "finished the spec"
        assert s.chat[-1]["changed"] == ["complete_priority"]
        assert s.chat[-1]["tokens"] == {"in": 200, "out": 40}

    first = sent[0]
    assert first["model"] == "test-chat-model"   # chat uses FOCUS_CHAT_MODEL, not FOCUS_MODEL
    assert first["messages"][0] == {"role": "system", "content": chat.SYSTEM_PROMPT}
    assert {t["function"]["name"] for t in first["tools"]} == set(chat._HANDLERS) | {"rate_reply"}
    assert all(t["type"] == "function" for t in first["tools"])
    assert "<data>" in first["messages"][-1]["content"]
    # The second call replays the assistant turn, then the tool's result.
    replayed, result = sent[1]["messages"][-2:]
    assert replayed["role"] == "assistant"
    assert replayed["tool_calls"][0]["function"]["name"] == "complete_priority"
    assert result["role"] == "tool" and result["tool_call_id"] == "call_1"
    assert "[done] Write spec" in result["content"]


@pytest.mark.parametrize("reply, replayed", [
    ("All set, pre‑emergent added; next is Car inspection.", "All set, pre‑emergent added."),
    ("Done. Next is Car inspection.", "Done."),
    ("Marked it done. Next up: the report", "Marked it done."),
    ("Next is Car inspection.", "Next is Car inspection."),   # nothing else to keep
    ("Start with the parser.", "Start with the parser."),
])
def test_replayed_replies_lose_their_closing_next_item(reply, replayed):
    assert chat._without_next(reply) == replayed


@pytest.mark.parametrize("text, first", [
    ("Car inspection marked done.Done.", "Car inspection marked done."),
    ("Marked done.All good.Let me know what's next.", "Marked done."),
    ("Done. Next is the report.", "Done. Next is the report."),
    ("Ship it to the U.S. office.", "Ship it to the U.S. office."),
])
def test_a_glued_on_second_message_is_dropped(text, first):
    assert chat._first_part(text) == first
    for size in (1, 3, 7):   # the same, streamed in pieces of any size
        sent = []
        stream = chat._FirstPartFilter(sent.append)
        for i in range(0, len(text), size):
            stream.feed(text[i:i + size])
        assert "".join(sent) == first


def test_next_item_is_not_added_twice(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Car inspection")
        actions.add_priority(s, "Call the dentist")
    client, _ = scripted(
        completion(tool_calls=[tool_call("complete_priority", {"id": 1})]),
        completion("Car inspection done; next is Call the dentist."),
    )
    assert chat.run(cfg, "car inspection done", client=client) == "Car inspection done; next is Call the dentist."


def test_next_item_is_named_only_when_the_top_changed(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Car inspection")
        actions.add_priority(s, "Lawn pre-emergent")

    client, _ = scripted(
        completion(tool_calls=[tool_call("complete_priority", {"id": 2})]),
        completion("Marked pre-emergent done."),
        completion(tool_calls=[tool_call("complete_priority", {"id": 1})]),
        completion("Car inspection is done."),
    )
    assert chat.run(cfg, "pre-emergent is done", client=client) == "Marked pre-emergent done."
    assert chat.run(cfg, "car inspection done", client=client) == \
        "Car inspection is done. That's everything on your list."
    with st.locked_state(cfg) as s:
        assert s.chat[-1]["reply"].endswith("That's everything on your list.")


def test_chat_model_defaults_to_the_check_in_model(cfg, monkeypatch):
    monkeypatch.delenv("FOCUS_CHAT_MODEL")
    client, sent = scripted(completion("Sure."))
    chat.run(config.load(), "hi", client=client)
    assert sent[0]["model"] == "test-model"


def test_streaming_hands_over_text_as_it_arrives(cfg):
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
        return httpx.Response(200, text=sse("<think>plan</think>Start with ", "the parser."),
                              headers={"content-type": "text/event-stream"})

    pieces = []
    reply = chat.run(cfg, "what first?", client=client_for(handler), on_text=pieces.append)
    assert "".join(pieces) == "Start with the parser."   # the thinking never reaches the speaker
    assert reply == "Start with the parser."
    with st.locked_state(cfg) as s:
        assert s.chat[-1]["tokens"] == {"in": 100, "out": 12}


def test_streamed_tool_calls_are_put_back_together(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
    split = [  # one call, its arguments split across three chunks
        {"index": 0, "id": "call_9", "type": "function", "function": {"name": "complete_priority", "arguments": ""}},
        {"index": 0, "function": {"arguments": '{"i'}},
        {"index": 0, "function": {"arguments": 'd": 1}'}},
    ]
    bodies = [sse(tool_calls=split), sse("Done.")]

    def handler(request):
        return httpx.Response(200, text=bodies.pop(0), headers={"content-type": "text/event-stream"})

    pieces = []
    reply = chat.run(cfg, "done", client=client_for(handler), on_text=pieces.append)
    # The code's own "what is next" sentence is streamed to the speaker too.
    assert reply == "".join(pieces) == "Done. That's everything on your list."
    with st.locked_state(cfg) as s:
        assert s.priorities[0].done


def test_bad_tool_input_is_reported_to_the_model(cfg):
    client, sent = scripted(
        completion(tool_calls=[tool_call("drop_priority", {"id": 99})]),
        completion("I couldn't find that one."),
    )
    chat.run(cfg, "drop 99", client=client)
    result = sent[1]["messages"][-1]
    assert result["content"].startswith("Error:") and "99" in result["content"]


@pytest.mark.parametrize("args, error", [
    ("not json", "not valid JSON"),
    ('{"id": "one"}', "whole number"),
    ('{"id": true}', "whole number"),
    ('["id", 1]', "JSON object"),
])
def test_malformed_tool_arguments_change_nothing(cfg, args, error):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
    client, sent = scripted(
        completion(tool_calls=[tool_call("complete_priority", args)]),
        completion("Sorry, let me try that again."),
    )
    chat.run(cfg, "done", client=client)
    assert error in sent[1]["messages"][-1]["content"]
    with st.locked_state(cfg) as s:
        assert not s.priorities[0].done
        assert s.chat[-1]["changed"] == []


def test_numeric_strings_and_long_text_are_tidied(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
    client, _ = scripted(
        completion(tool_calls=[tool_call("complete_priority", '{"id": "1"}', "call_1"),
                               tool_call("add_priority", {"text": "  " + "x" * 500}, "call_2")]),
        completion("Done."),
    )
    chat.run(cfg, "done, and add the long one", client=client)
    with st.locked_state(cfg) as s:
        assert s.priorities[0].done
        assert s.priorities[1].text == "x" * chat.MAX_PRIORITY_CHARS


def test_history_is_replayed_as_plain_text(cfg):
    client, _ = scripted(completion("Sure."))
    chat.run(cfg, "first", client=client)
    client, sent = scripted(completion("Again."))
    chat.run(cfg, "second", client=client)
    msgs = sent[0]["messages"]
    assert msgs[1] == {"role": "user", "content": "first"}
    assert msgs[2] == {"role": "assistant", "content": "Sure."}
    assert msgs[3]["content"].endswith("second")


def test_runaway_tool_loop_stops(cfg):
    looping = [completion(tool_calls=[tool_call("report", {"how": "on_track"}, f"call_{i}")])
               for i in range(chat.MAX_STEPS)]
    client, _ = scripted(*looping)
    with pytest.raises(CoachError, match="too many steps"):
        chat.run(cfg, "hi", client=client)
    with st.locked_state(cfg) as s:
        assert s.chat == []


def test_daily_limit(cfg):
    client, _ = scripted(completion("ok"))
    chat.run(cfg, "one", client=client, daily_limit=1)
    with pytest.raises(CoachError, match="limit"):
        chat.run(cfg, "two", client=client, daily_limit=1)


def test_failed_messages_count_toward_the_daily_limit(cfg):
    client = client_for(lambda r: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(CoachError, match="500"):
        chat.run(cfg, "one", client=client, daily_limit=1)
    with pytest.raises(CoachError, match="limit"):
        chat.run(cfg, "two", client=client, daily_limit=1)
    with st.locked_state(cfg) as s:
        assert s.chat == [] and s.chat_attempts == 1


def test_attempts_reset_at_rollover_and_default_from_old_state(cfg):
    from datetime import date
    old = st.State.from_dict({"version": st.SCHEMA_VERSION, "date": "2026-10-14",
                              "chat": [{"at": "x", "user": "hi", "reply": "hey"}]})
    assert old.chat_attempts == 1  # a state file from before the counter existed
    with st.locked_state(cfg, today=date(2026, 10, 14)) as s:
        s.chat_attempts = 7
    with st.locked_state(cfg, today=date(2026, 10, 15)) as s:
        assert s.chat_attempts == 0


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
            raise CoachError("The coach is offline: can't reach the model provider")
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
    assert post(f"{base}/chat", b'{"text": "fail"}') == (
        200, "Sorry, that didn't work: The coach is offline: can't reach the model provider")
    assert post(f"{base}/chat", b'{"text": "x"}', token="wrong")[0] == 403
    assert post(f"{base}/chat", b'not json')[0] == 400
    assert post(f"{base}/other", b'{"text": "x"}')[0] == 404
    assert calls == ["hello", "fail"]


def test_voice_messages_ask_for_short_replies(cfg):
    client, sent = scripted(completion("Done. Next is Email Sam."))
    chat.run(cfg, "done with the spec", client=client, voice=True)
    assert sent[0]["messages"][0]["content"] == chat.SYSTEM_PROMPT + chat.VOICE_RULES
    with st.locked_state(cfg) as s:
        assert s.chat[-1]["voice"] is True
        assert s.chat[-1]["model"] == "test-chat-model"
