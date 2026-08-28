"""kindo-asr Provider 客户端（技术方案 §11.2）。

HTTP 契约（第一方内部接口）：
  POST /asr/utterances                  {utterance_id, format, sample_rate, channels}
  POST /asr/utterances/{uid}/feed       binary PCM
  POST /asr/utterances/{uid}/finish  -> {text, confidence?, language?, metadata}
  POST /asr/utterances/{uid}/cancel
  GET  /health                       -> {status, model, ready}
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..errors import provider_unavailable

logger = logging.getLogger("kindo.asr")


class AsrFinal:
    def __init__(self, data: dict):
        self.text: str = (data.get("text") or "").strip()
        self.confidence: float | None = data.get("confidence")
        self.language: str | None = data.get("language")
        self.metadata: dict = data.get("metadata") or {}


class AsrProviderClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 10.0):
        self._endpoint = endpoint.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    @property
    def configured(self) -> bool:
        return bool(self._endpoint)

    async def start_utterance(self, utterance_id: str, audio_format: str = "pcm_s16le",
                              sample_rate: int = 16000, channels: int = 1) -> None:
        r = await self._post("/asr/utterances", {
            "utterance_id": utterance_id, "format": audio_format,
            "sample_rate": sample_rate, "channels": channels,
        })
        if r.status_code not in (200, 201, 202):
            raise provider_unavailable(f"kindo-asr start 失败: {r.status_code}")

    async def feed(self, utterance_id: str, pcm: bytes) -> None:
        r = await self._client.post(
            f"{self._endpoint}/asr/utterances/{utterance_id}/feed", content=pcm
        )
        if r.status_code not in (200, 201, 202):
            raise provider_unavailable(f"kindo-asr feed 失败: {r.status_code}")

    async def finish(self, utterance_id: str) -> AsrFinal:
        r = await self._post(f"/asr/utterances/{utterance_id}/finish", {})
        if r.status_code != 200:
            raise provider_unavailable(f"kindo-asr finish 失败: {r.status_code}")
        return AsrFinal(r.json())

    async def cancel(self, utterance_id: str) -> None:
        try:
            await self._post(f"/asr/utterances/{utterance_id}/cancel", {})
        except Exception:
            pass

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"status": "not_configured", "ready": False}
        try:
            r = await self._client.get(f"{self._endpoint}/health", timeout=3.0)
            if r.status_code == 200:
                return r.json()
            return {"status": "unhealthy", "ready": False, "http": r.status_code}
        except Exception as exc:
            return {"status": "unreachable", "ready": False, "error": str(exc)[:120]}

    async def _post(self, path: str, payload: dict) -> httpx.Response:
        return await self._client.post(f"{self._endpoint}{path}", json=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
