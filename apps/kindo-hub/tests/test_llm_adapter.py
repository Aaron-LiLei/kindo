"""LLM Adapter：SSE 解析 / 工具调用聚合 / 错误路径（技术方案 §11.3）。"""
import json

import httpx

from kindo.config import LLMProviderConfig
from kindo.providers.llm import OpenAIChatCompletionsAdapter, accumulate_tool_calls

PROVIDER = LLMProviderConfig({
    "id": "t", "display_name": "t", "protocol": "openai_chat_completions",
    "base_url": "http://llm.test/v1", "model": "m",
})


def sse(*payloads) -> bytes:
    out = b""
    for p in payloads:
        out += f"data: {json.dumps(p)}\n\n".encode()
    return out + b"data: [DONE]\n\n"


def text_chunk(content, finish=None):
    delta = {"content": content}
    choice = {"delta": delta, "index": 0}
    if finish:
        choice["finish_reason"] = finish
    return {"choices": [choice]}


def tool_chunk(index, tid=None, name=None, args=None):
    tc = {"index": index}
    if tid:
        tc["id"] = tid
    fn = {}
    if name:
        fn["name"] = name
    if args:
        fn["arguments"] = args
    tc["function"] = fn
    return {"choices": [{"delta": {"tool_calls": [tc]}, "index": 0}]}


def make_transport(response_bytes: bytes, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=response_bytes,
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


async def test_text_stream_parsing(monkeypatch):
    adapter = OpenAIChatCompletionsAdapter()
    body = sse(text_chunk("你"), text_chunk("好"), text_chunk("呀", finish="stop"))
    # 注入 mock transport
    import kindo.providers.llm as llm_mod
    orig = llm_mod.httpx.AsyncClient

    def patched_client(*a, **kw):
        kw["transport"] = make_transport(body)
        return orig(*a, **kw)

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", patched_client)
    events = [e async for e in adapter.generate(PROVIDER, [{"role": "user", "content": "hi"}],
                                                None, "req-1")]
    texts = [e.text for e in events if e.type == "text_delta"]
    assert texts == ["你", "好", "呀"]
    assert events[-1].type == "completed"


async def test_tool_call_accumulation(monkeypatch):
    adapter = OpenAIChatCompletionsAdapter()
    body = sse(
        tool_chunk(0, tid="call_1", name="search_media", args='{"que'),
        tool_chunk(0, args='ry": "汪汪队"}'),
        text_chunk("找到了", finish="tool_calls"),
    )
    import kindo.providers.llm as llm_mod
    orig = llm_mod.httpx.AsyncClient

    def patched_client(*a, **kw):
        kw["transport"] = make_transport(body)
        return orig(*a, **kw)

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", patched_client)
    events = [e async for e in adapter.generate(PROVIDER, [], [], "req-2")]
    calls = accumulate_tool_calls(events)
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "search_media"
    assert calls[0].arguments == '{"query": "汪汪队"}'


async def test_http_error_yields_error_event(monkeypatch):
    adapter = OpenAIChatCompletionsAdapter()
    import kindo.providers.llm as llm_mod
    orig = llm_mod.httpx.AsyncClient

    def patched_client(*a, **kw):
        kw["transport"] = make_transport(b'{"error": "x"}', status=401)
        return orig(*a, **kw)

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", patched_client)
    events = [e async for e in adapter.generate(PROVIDER, [], None, "req-3")]
    assert events[0].type == "error"
    assert "401" in events[0].error


def test_accumulate_multiple_tools():
    from kindo.providers.llm import LlmEvent

    events = [
        LlmEvent("tool_call_delta", tool_index=0, tool_id="c1", tool_name="search_media",
                 args_delta='{"query":'),
        LlmEvent("tool_call_delta", tool_index=0, args_delta=' "a"}'),
        LlmEvent("tool_call_delta", tool_index=1, tool_id="c2", tool_name="play_media",
                 args_delta='{"media_id": "m1", "action": "play"}'),
    ]
    calls = accumulate_tool_calls(events)
    assert [c.name for c in calls] == ["search_media", "play_media"]
    assert json.loads(calls[1].arguments) == {"media_id": "m1", "action": "play"}


def test_url_normalization():
    adapter = OpenAIChatCompletionsAdapter()
    assert adapter._url("http://x/v1") == "http://x/v1/chat/completions"
    assert adapter._url("http://x/v1/") == "http://x/v1/chat/completions"
    assert adapter._url("http://x/v1/chat/completions") == "http://x/v1/chat/completions"
