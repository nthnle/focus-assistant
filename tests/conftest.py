import json

import httpx
import openai
import pytest


@pytest.fixture(autouse=True)
def models(monkeypatch):
    """Every test runs against named models and no real provider."""
    monkeypatch.setenv("FOCUS_MODEL", "test-model")
    monkeypatch.setenv("FOCUS_CHAT_MODEL", "test-chat-model")
    for name in ("FOCUS_LLM_BASE_URL", "FOCUS_LLM_API_KEY", "FOCUS_EFFORT"):
        monkeypatch.delenv(name, raising=False)


def completion(text=None, tool_calls=(), model="test-model", finish_reason=None, usage=(100, 20)):
    """A chat-completions response body."""
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return {
        "id": "cmpl_1", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop")}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                  "total_tokens": usage[0] + usage[1]},
    }


def tool_call(name, args, id_="call_1"):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


def client_for(handler):
    return openai.OpenAI(base_url="http://provider.test/v1", api_key="test", max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def scripted(*bodies):
    """A client that answers with these bodies in order and records each request."""
    sent = []
    queue = list(bodies)

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0))

    return client_for(handler), sent


def sse(*pieces, model="test-model", usage=(100, 12), tool_calls=()):
    """A streamed reply as server-sent events: text pieces, then optional tool-call pieces."""
    def chunk(delta, finish=None, with_usage=False):
        body = {"id": "cmpl_1", "object": "chat.completion.chunk", "created": 0, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if with_usage:
            body["choices"] = []
            body["usage"] = {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                             "total_tokens": sum(usage)}
        return f"data: {json.dumps(body)}\n\n"

    out = [chunk({"role": "assistant", "content": ""})]
    out += [chunk({"content": p}) for p in pieces]
    out += [chunk({"tool_calls": [tc]}) for tc in tool_calls]
    out.append(chunk({}, finish="tool_calls" if tool_calls else "stop"))
    out.append(chunk({}, with_usage=True))
    out.append("data: [DONE]\n\n")
    return "".join(out)
