"""TTS Provider（技术方案 §11.4）。V0.1 默认 Android 系统 TTS：render 返回
DeviceRenderInstruction，实际执行端（TV）必须回报 tts_started / finished / interrupted。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TtsRenderInstruction:
    provider: str  # android_tts
    tts_id: str
    text: str
    locale: str | None = None
    voice_hint: str | None = None

    def to_payload(self) -> dict:
        return {
            "tts_id": self.tts_id,
            "provider": self.provider,
            "text": self.text,
            "locale": self.locale,
            "voice_hint": self.voice_hint,
        }


class TtsService:
    """V0.1：DeviceRenderInstruction(android_tts)。TTS 不可用时由 TV 降级为文本显示。"""

    provider_name = "android_tts"

    def render(self, tts_id: str, text: str, locale: str | None = None,
               voice_hint: str | None = None) -> TtsRenderInstruction:
        return TtsRenderInstruction(
            provider=self.provider_name, tts_id=tts_id, text=text,
            locale=locale, voice_hint=voice_hint,
        )
