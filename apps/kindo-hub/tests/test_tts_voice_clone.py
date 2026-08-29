"""家长声音克隆 TTS 路由（PRD TTS-005~007 / 技术方案 §6.7）。

覆盖：TtsService 按句路由（hub_tts 成功/回退/熔断/缓存）与
/admin/voice-profile 管理面（录入校验、试听、删除、鉴权）。
"""
from __future__ import annotations

import asyncio
import io
import wave

import pytest

from kindo.providers.tts import HubTtsClient, TtsService

# ---------- TtsService 路由 ----------

class FakeVoiceStore:
    def __init__(self, exists: bool = True):
        self._exists = exists

    def exists(self) -> bool:
        return self._exists

    def load(self):
        return None

    def wav_base64(self) -> str:
        return "cHJvbXB0"

    def prompt_text(self) -> str:
        return "参考文本"

    def fingerprint(self) -> str:
        return "fp1"

    def delete(self) -> bool:
        return self._exists


class FakeHubTtsClient(HubTtsClient):
    """覆盖 HTTP 行为的假客户端（复用 TtsService 依赖面）。"""

    def __init__(self, *, ready: bool = True, voice_loaded: bool = True,
                 fail: bool = False):
        super().__init__("")  # 不发真实 HTTP
        self._endpoint = "http://kindo-tts-test:8092"  # 使 configured=True
        self._ready = ready
        self._voice_loaded = voice_loaded
        self._fail = fail
        self.ensure_calls = 0
        self.synth_calls = 0

    async def health(self) -> dict:  # noqa: D102
        return {"status": "ok" if self._ready else "no_model",
                "ready": self._ready, "voice_loaded": self._voice_loaded}

    async def ensure_voice(self, wav_base64: str, prompt_text: str) -> bool:  # noqa: D102
        self.ensure_calls += 1
        return self._ready

    async def synthesize(self, tts_id: str, text: str) -> bytes:  # noqa: D102
        self.synth_calls += 1
        if self._fail:
            raise RuntimeError("synthesis_failed")
        return b"wav-" + tts_id.encode()


def _render(svc: TtsService, tts_id: str = "t1", text: str = "你好呀"):
    return asyncio.run(svc.render(tts_id, text))


def test_unconfigured_falls_back_to_android_tts():
    svc = TtsService()
    inst = _render(svc, "t1", "你好")
    assert inst.provider == "android_tts"
    assert inst.audio_path is None
    assert inst.to_payload()["provider"] == "android_tts"
    assert "audio_path" not in inst.to_payload()


def test_hub_tts_success_payload_and_audio_cache():
    client = FakeHubTtsClient()
    svc = TtsService(hub_tts=client, voice_store=FakeVoiceStore())
    inst = _render(svc, "t1", "你好")
    assert inst.provider == "hub_tts"
    payload = inst.to_payload()
    assert payload["audio_path"] == "/api/v1/tts/t1/audio"
    assert payload["text"] == "你好"  # text 仍必发（TV 回退依据）
    assert svc.get_audio("t1") == b"wav-t1"
    # 声纹只推一次（fingerprint 未变）
    _render(svc, "t2", "第二句")
    assert client.ensure_calls == 1
    svc.drop_tts(["t1", "t2"])  # 会话结束即清（§6.7）
    assert svc.get_audio("t1") is None
    assert svc.get_audio("t2") is None


def test_hub_tts_synthesis_failure_falls_back():
    client = FakeHubTtsClient(fail=True)
    svc = TtsService(hub_tts=client, voice_store=FakeVoiceStore())
    inst = _render(svc, "t1", "你好")
    assert inst.provider == "android_tts"
    assert svc.get_audio("t1") is None
    assert client.synth_calls == 1


def test_hub_tts_breaker_cooldown_skips_clone():
    client = FakeHubTtsClient(fail=True)
    svc = TtsService(hub_tts=client, voice_store=FakeVoiceStore())
    _render(svc, "t1", "第一句")
    _render(svc, "t2", "第二句")
    assert client.synth_calls == 2  # 连续 2 次失败进入冷却
    inst = _render(svc, "t3", "第三句")
    assert inst.provider == "android_tts"
    assert client.synth_calls == 2  # 冷却期内不再尝试克隆
    svc.invalidate_voice()  # 冷却解除（样本变更同语义）
    inst = _render(svc, "t4", "第四句")
    assert inst.provider == "android_tts"  # 仍失败（fail=True）
    assert client.synth_calls == 3


def test_hub_tts_not_ready_or_no_voice_falls_back():
    svc = TtsService(hub_tts=FakeHubTtsClient(ready=False), voice_store=FakeVoiceStore())
    assert _render(svc, "t1", "你好").provider == "android_tts"
    svc2 = TtsService(hub_tts=FakeHubTtsClient(), voice_store=FakeVoiceStore(exists=False))
    assert _render(svc2, "t1", "你好").provider == "android_tts"


def test_audio_ttl_expiry():
    client = FakeHubTtsClient()
    svc = TtsService(hub_tts=client, voice_store=FakeVoiceStore())
    _render(svc, "t1", "你好")
    wav, _ = svc._audio["t1"]
    svc._audio["t1"] = (wav, 0.0)  # 人为过期
    assert svc.get_audio("t1") is None


# ---------- Admin /voice-profile ----------

def _wav_bytes(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture()
def admin_env(env):
    env.bootstrap_admin()
    return env


def test_voice_profile_requires_admin(env):
    r = env.client.get("/api/v1/admin/voice-profile")
    assert r.status_code in (401, 403)


def test_voice_profile_empty_state(admin_env):
    r = admin_env.client.get("/api/v1/admin/voice-profile",
                             headers=admin_env.admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["voice_profile"]["configured"] is False
    assert body["clone_ready"] is False
    assert body["configured"] is False  # 未配置 kindo-tts endpoint


def test_voice_profile_upload_validate_delete(admin_env):
    headers = admin_env.admin_headers()
    # 过短录音（1s < 3s）被拒
    r = admin_env.client.put(
        "/api/v1/admin/voice-profile", headers=headers,
        files={"audio": ("rec.wav", _wav_bytes(1.0), "audio/wav")},
        data={"prompt_text": "各位村民，大家新年好！"},
    )
    assert r.status_code == 400
    # 合规录音（5s）→ 转码 24kHz 单声道
    r = admin_env.client.put(
        "/api/v1/admin/voice-profile", headers=headers,
        files={"audio": ("rec.wav", _wav_bytes(5.0), "audio/wav")},
        data={"prompt_text": "各位村民，大家新年好！"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice_profile"]["configured"] is True
    assert 4.5 <= body["voice_profile"]["duration_seconds"] <= 5.5
    assert body["voice_profile"]["sample_rate"] == 24000
    # 空文本被拒
    r = admin_env.client.put(
        "/api/v1/admin/voice-profile", headers=headers,
        files={"audio": ("rec.wav", _wav_bytes(5.0), "audio/wav")},
        data={"prompt_text": "  "},
    )
    assert r.status_code == 400
    # 试听
    r = admin_env.client.get("/api/v1/admin/voice-profile/audio", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content), "rb") as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
    # 删除
    r = admin_env.client.delete("/api/v1/admin/voice-profile", headers=headers)
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    r = admin_env.client.get("/api/v1/admin/voice-profile/audio", headers=headers)
    assert r.status_code == 404


def test_voice_profile_device_token_rejected(env):
    env.bootstrap_admin()
    _, token = env.pair_device()
    env.client.cookies.clear()  # 摘掉管理会话 Cookie，仅凭 Device Token 访问
    r = env.client.get("/api/v1/admin/voice-profile",
                       headers=env.device_headers(token))
    assert r.status_code in (401, 403)
