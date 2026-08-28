"""LLM Provider：openai_chat_completions 必备 Adapter（技术方案 §11.3）。

generate() 返回内部事件流（TextDelta / ToolCallDelta / Completed / Error）；
外部 SSE 字段差异在 Adapter 内归一化；Secret 只在服务端使用，不写日志。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import LLMProviderConfig

logger = logging.getLogger("kindo.llm")


@dataclass
class LlmEvent:
    type: str  # text_delta | tool_call_delta | completed | error
    text: str | None = None
    tool_index: int | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    args_delta: str | None = None
    finish_reason: str | None = None
    error: str | None = None


@dataclass
class ToolCallResult:
    id: str
    name: str
    arguments: str
    parsed: dict = field(default_factory=dict)


class OpenAIChatCompletionsAdapter:
    def __init__(self, connect_timeout: int = 5, first_event_timeout: int = 15,
                 total_timeout: int = 30):
        self.connect_timeout = connect_timeout
        self.first_event_timeout = first_event_timeout
        self.total_timeout = total_timeout

    def _url(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    async def generate(
        self,
        provider: LLMProviderConfig,
        messages: list[dict],
        tools: list[dict] | None,
        request_id: str,
        *,
        no_store: bool = True,
    ) -> AsyncIterator[LlmEvent]:
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": False},
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        if no_store:
            body["store"] = False
        headers = {
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        }
        if provider.api_key:  # 空 key（本地/测试端点）不发送 Authorization
            headers["Authorization"] = f"Bearer {provider.api_key}"
        timeout = httpx.Timeout(self.total_timeout, connect=self.connect_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", self._url(provider.base_url), json=body, headers=headers
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:300]
                    yield LlmEvent(type="error", error=f"llm_http_{response.status_code}: {detail}")
                    return
                first = True
                async for line in response.aiter_lines():
                    if first:
                        first = False  # first_event_timeout 由 httpx read 超时近似覆盖
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield LlmEvent(type="completed", finish_reason="stop")
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for event in self._parse_chunk(chunk):
                        yield event
                        if event.type == "completed":
                            return
                yield LlmEvent(type="completed", finish_reason="stop")

    def _parse_chunk(self, chunk: dict) -> list[LlmEvent]:
        events: list[LlmEvent] = []
        choices = chunk.get("choices") or []
        if not choices:
            return events
        choice = choices[0]
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            events.append(LlmEvent(type="text_delta", text=content))
        for tc in delta.get("tool_calls") or []:
            idx = int(tc.get("index", 0))
            fn = tc.get("function") or {}
            events.append(LlmEvent(
                type="tool_call_delta",
                tool_index=idx,
                tool_id=tc.get("id"),
                tool_name=fn.get("name"),
                args_delta=fn.get("arguments"),
            ))
        if choice.get("finish_reason"):
            events.append(LlmEvent(type="completed", finish_reason=choice["finish_reason"]))
        return events


def accumulate_tool_calls(events: list[LlmEvent]) -> list[ToolCallResult]:
    """把流式 tool_call_delta 聚合为完整调用列表；参数在完成后由 Tool Runtime 校验。"""
    ordered: dict[int, dict] = {}
    for ev in events:
        if ev.type != "tool_call_delta" or ev.tool_index is None:
            continue
        slot = ordered.setdefault(ev.tool_index, {"id": "", "name": "", "args": ""})
        if ev.tool_id:
            slot["id"] = ev.tool_id
        if ev.tool_name:
            slot["name"] = ev.tool_name
        if ev.args_delta:
            slot["args"] += ev.args_delta
    out: list[ToolCallResult] = []
    for idx in sorted(ordered):
        slot = ordered[idx]
        call_id = slot["id"] or f"call_{idx}"
        out.append(ToolCallResult(id=call_id, name=slot["name"], arguments=slot["args"]))
    return out


async def with_first_event_timeout(coro_iter, timeout: int):
    """对首个事件施加 first_event_timeout。"""
    it = coro_iter.__aiter__()
    try:
        first = await asyncio.wait_for(it.__anext__(), timeout=timeout)
    except TimeoutError:
        yield LlmEvent(type="error", error=f"llm_first_event_timeout_{timeout}s")
        return
    except StopAsyncIteration:
        return
    yield first
    async for ev in it:
        yield ev
