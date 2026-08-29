"""TTS Provider（技术方案 §11.4/§6.7）。默认 Android 系统 TTS：render 返回
DeviceRenderInstruction，实际执行端（TV）必须回报 tts_started / finished / interrupted。

v0.3.6 增补 hub_tts（PRD TTS-005~007）：家长声音样本存在且 kindo-tts 可用时按句
本地克隆合成，payload 增 audio_path（TV 经设备 Bearer 鉴权拉取播放）；任何失败/
超时/冷却期自动回退 android_tts 原语义——事件与追问窗口驱动完全不变。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from ..errors import provider_unavailable

if TYPE_CHECKING:
    from ..voice_profile import VoiceStore

logger = logging.getLogger("kindo.tts")


@dataclass
class TtsRenderInstruction:
    provider: str  # android_tts | hub_tts
    tts_id: str
    text: str
    locale: str | None = None
    voice_hint: str | None = None
    audio_path: str | None = None  # hub_tts 专用：TV 拉取音频路径（设备 Bearer 鉴权）

    def to_payload(self) -> dict:
        payload: dict[str, Any] = {
            "tts_id": self.tts_id,
            "provider": self.provider,
            "text": self.text,
            "locale": self.locale,
            "voice_hint": self.voice_hint,
        }
        if self.audio_path:
            payload["audio_path"] = self.audio_path
        return payload


class HubTtsClient:
    """kindo-tts 容器客户端（技术方案 §6.7 契约）。

    PUT /v1/voice {prompt_wav_base64, prompt_text} -> 204
    DELETE /v1/voice -> 204
    POST /v1/synthesis {tts_id, text} -> audio/wav
    GET /health -> {status, model, ready, voice_loaded}
    """

    def __init__(self, endpoint: str, timeout_seconds: float = 20.0):
        self._endpoint = (endpoint or "").rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    @property
    def configured(self) -> bool:
        return bool(self._endpoint)

    async def health(self) -> dict:
        r = await self._client.get(f"{self._endpoint}/health", timeout=3.0)
        r.raise_for_status()
        return r.json()

    async def ensure_voice(self, wav_base64: str, prompt_text: str) -> bool:
        """确保容器已载入当前声纹；返回是否可用（不 ready 或推送失败均 False）。"""
        h = await self.health()
        if not h.get("ready"):
            return False
        if h.get("voice_loaded"):
            return True
        r = await self._client.put(f"{self._endpoint}/v1/voice",
                                   json={"prompt_wav_base64": wav_base64,
                                         "prompt_text": prompt_text})
        if r.status_code != 204:
            raise provider_unavailable(f"kindo-tts 声纹推送失败: {r.status_code}")
        return True

    async def synthesize(self, tts_id: str, text: str) -> bytes:
        r = await self._client.post(
            f"{self._endpoint}/v1/synthesis", json={"tts_id": tts_id, "text": text}
        )
        if r.status_code != 200:
            raise provider_unavailable(f"kindo-tts 合成失败: {r.status_code}")
        return r.content

    async def clear_voice(self) -> None:
        """尽力清除容器内存声纹（样本删除时调用；失败仅告警）。"""
        try:
            await self._client.delete(f"{self._endpoint}/v1/voice")
        except Exception as exc:
            logger.warning("kindo-tts 声纹清除失败（将随下次健康探测重推/覆盖）: %s", exc)

    async def aclose(self) -> None:
        await self._client.aclose()


class TtsService:
    """按句路由：hub_tts（克隆，可选）优先，android_tts 兜底（§6.7 下发语义）。"""

    provider_name = "android_tts"
    HUB_TTS_PROVIDER = "hub_tts"

    _AUDIO_TTL_SECONDS = 600.0   # 会话内播放窗口远短于此；会话结束由 drop_tts 显式清
    _AUDIO_MAX_ENTRIES = 32
    _BREAK_AFTER_FAILURES = 2
    _BREAK_COOLDOWN_SECONDS = 60.0

    def __init__(self, hub_tts: HubTtsClient | None = None,
                 voice_store: VoiceStore | None = None):
        self._hub_tts = hub_tts
        self._voice_store = voice_store
        self._audio: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._consecutive_failures = 0
        self._cooldown_until = 0.0
        self._pushed_fingerprint: str | None = None
        # render 在编排工作循环、get_audio/drop_tts/invalidate_voice 在 FastAPI
        # 线程池——共享可变状态必须持锁（锁绝不跨越 await）
        self._state_lock = threading.Lock()

    @property
    def hub_tts_configured(self) -> bool:
        return self._hub_tts is not None and self._hub_tts.configured

    def _clone_available(self) -> bool:
        with self._state_lock:
            in_cooldown = time.monotonic() < self._cooldown_until
        return (
            self.hub_tts_configured
            and self._voice_store is not None
            and self._voice_store.exists()
            and not in_cooldown
        )

    async def render(self, tts_id: str, text: str, locale: str | None = None,
                     voice_hint: str | None = None) -> TtsRenderInstruction:
        """合成成功 → hub_tts 指令；任何失败回退 android_tts（§6.7）。"""
        if self._clone_available():
            try:
                wav = await self._synthesize_with_voice(tts_id, text)
            except Exception as exc:
                with self._state_lock:
                    self._consecutive_failures += 1
                    failures = self._consecutive_failures
                    if failures >= self._BREAK_AFTER_FAILURES:
                        self._cooldown_until = time.monotonic() + self._BREAK_COOLDOWN_SECONDS
                        self._consecutive_failures = 0
                logger.warning("hub_tts 合成失败，本句回退系统 TTS（连续失败 %d）: %s",
                               failures, exc)
            else:
                with self._state_lock:
                    self._consecutive_failures = 0
                    self._audio[tts_id] = (wav, time.monotonic() + self._AUDIO_TTL_SECONDS)
                    self._trim_audio()
                return TtsRenderInstruction(
                    provider=self.HUB_TTS_PROVIDER, tts_id=tts_id, text=text,
                    locale=locale, voice_hint=voice_hint,
                    audio_path=f"/api/v1/tts/{tts_id}/audio",
                )
        return TtsRenderInstruction(provider=self.provider_name, tts_id=tts_id, text=text,
                                    locale=locale, voice_hint=voice_hint)

    async def _synthesize_with_voice(self, tts_id: str, text: str) -> bytes:
        assert self._hub_tts is not None and self._voice_store is not None
        fingerprint = self._voice_store.fingerprint()
        if fingerprint != self._pushed_fingerprint:
            ok = await self._hub_tts.ensure_voice(
                self._voice_store.wav_base64() or "", self._voice_store.prompt_text()
            )
            if not ok:
                raise provider_unavailable("kindo-tts 未就绪（no_model 或不可达）")
            self._pushed_fingerprint = fingerprint
        return await self._hub_tts.synthesize(tts_id, text)

    # ---------- 音频缓存（TV 拉取） ----------

    def get_audio(self, tts_id: str) -> bytes | None:
        with self._state_lock:
            entry = self._audio.get(tts_id)
            if entry is None:
                return None
            wav, expires = entry
            if time.monotonic() > expires:
                self._audio.pop(tts_id, None)
                return None
            self._audio.move_to_end(tts_id)
            return wav

    def drop_tts(self, tts_ids) -> None:
        """会话结束即清（§6.7）：丢弃该会话全部缓存音频。"""
        with self._state_lock:
            for tts_id in tts_ids:
                self._audio.pop(tts_id, None)

    def _trim_audio(self) -> None:
        # 调用方持有 _state_lock
        now = time.monotonic()
        for tts_id in [k for k, (_, exp) in self._audio.items() if now > exp]:
            self._audio.pop(tts_id, None)
        while len(self._audio) > self._AUDIO_MAX_ENTRIES:
            self._audio.popitem(last=False)

    # ---------- 样本生命周期 ----------

    def invalidate_voice(self) -> None:
        """样本变更/删除后失效推送缓存与熔断（下次 render 重推或回退）。"""
        with self._state_lock:
            self._pushed_fingerprint = None
            self._consecutive_failures = 0
            self._cooldown_until = 0.0

    async def remote_health(self) -> dict:
        """kindo-tts 健康探测（Admin 展示用；未配置返回 not_configured）。"""
        if not self.hub_tts_configured:
            return {"status": "not_configured", "ready": False, "voice_loaded": False}
        assert self._hub_tts is not None
        try:
            return await self._hub_tts.health()
        except Exception as exc:
            return {"status": "unreachable", "ready": False, "voice_loaded": False,
                    "error": str(exc)[:120]}

    async def purge_remote_voice(self) -> None:
        """样本删除时同步清除 kindo-tts 内存声纹（尽力而为）。"""
        if self._hub_tts is not None and self._hub_tts.configured:
            await self._hub_tts.clear_voice()

    def clone_status(self) -> dict:
        """Admin 可见态：是否可克隆（样本+配置+熔断）。"""
        store = self._voice_store
        profile = store.load() if store is not None else None
        with self._state_lock:
            in_cooldown = time.monotonic() < self._cooldown_until
        return {
            "configured": self.hub_tts_configured,
            "voice_profile": profile.public() if profile else {"configured": False},
            "clone_ready": bool(self._clone_available()),
            "in_cooldown": in_cooldown,
        }

    async def aclose(self) -> None:
        if self._hub_tts is not None:
            await self._hub_tts.aclose()
