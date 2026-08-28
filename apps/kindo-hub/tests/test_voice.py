"""Voice WS：VAD 分段、utterance 生命周期、ASR 契约（技术方案 §5）。

kindo-asr 契约用一个本地 FastAPI stub 服务承载（明确标注为测试替身，
覆盖 Hub 侧背压/超时/降级行为）；真实模型转写由 kindo-asr 自己的脚本验证。
"""
import struct
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request

ASR_STUB_PORTHolder = {"port": 18500}


class StubAsrServer:
    """实现 kindo-asr HTTP 契约的可编程测试替身。"""

    def __init__(self):
        self.app = FastAPI()
        self.utterances: dict[str, bytearray] = {}
        self.finished: list[str] = []
        self.fail_mode = None
        self.final_text = "我想看汪汪队"

        @self.app.get("/health")
        async def health():
            return {"status": "ready", "ready": True, "model": "stub"}

        @self.app.post("/asr/utterances")
        async def start(request: Request):
            body = await request.json()
            uid = body["utterance_id"]
            self.utterances[uid] = bytearray()
            return {"accepted": True}

        @self.app.post("/asr/utterances/{uid}/feed")
        async def feed(uid: str, request: Request):
            if uid not in self.utterances:
                raise HTTPException(404)
            self.utterances[uid].extend(await request.body())
            return {}

        @self.app.post("/asr/utterances/{uid}/finish")
        async def finish(uid: str):
            if uid not in self.utterances:
                raise HTTPException(404)
            self.finished.append(uid)
            self.utterances.pop(uid)
            if self.fail_mode == "unreachable":
                raise HTTPException(503)
            return {"text": self.final_text, "confidence": 0.95, "language": "zh"}

        @self.app.post("/asr/utterances/{uid}/cancel")
        async def cancel(uid: str):
            self.utterances.pop(uid, None)
            return {}

    def start(self, port: int) -> None:
        self._server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=port, log_level="error"))
        self._thread = threading.Thread(
            target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._server.started:
                return
            time.sleep(0.1)
        raise RuntimeError("stub asr 启动失败")


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (16000 * 2 * ms // 1000 // 2)


def _tone(ms: int, freq=440, amp=12000) -> bytes:
    import math

    n = 16000 * ms // 1000
    out = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / 16000))
        out += struct.pack("<h", v)
    return bytes(out)


@pytest.fixture()
def voice_env(env):
    stub = StubAsrServer()
    port = 18100 + (id(env) % 500)
    stub.start(port)
    env.reconfigure(asr_endpoint=f"http://127.0.0.1:{port}")
    # 重建 ASR 客户端指向 stub
    from kindo.providers.asr import AsrProviderClient

    env.state.asr.aclose()
    env.state.asr = AsrProviderClient(f"http://127.0.0.1:{port}", 5.0)
    yield env, stub
    stub._server.should_exit = True


def _make_session(env, token):
    """配置一个 LLM provider 指向不存在的地址（语音测试不真正调 LLM）。"""
    from kindo.config import LLMProviderConfig

    env.state.config.llm_providers = [LLMProviderConfig({
        "id": "main", "display_name": "x", "protocol": "openai_chat_completions",
        "base_url": "http://127.0.0.1:19998/v1", "model": "m",
    })]
    env.state.provider_registry.reload()  # 会话读 registry，需同步注入
    r = env.client.post(
        "/api/v1/conversations", json={}, headers=env.device_headers(token))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_voice_open_requires_valid_session(voice_env):
    env, _stub = voice_env
    env.bootstrap_admin()
    _d, token = env.pair_device()
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect(f"/api/v1/voice?token={token}&session_id=bad"):
            pass


def test_voice_vad_transcribes_and_emits_asr_final(voice_env):
    """语音 → VAD（说话/停顿）→ ASR final → asr.final 事件（Realtime 实时接收）。"""
    env, stub = voice_env
    env.bootstrap_admin()
    _d, token = env.pair_device()
    session_id = _make_session(env, token)

    import json

    received: list[dict] = []
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as realtime:
        with env.client.websocket_connect(
            f"/api/v1/voice?token={token}&session_id={session_id}"
        ) as voice:
            voice.send_json({"type": "voice.open", "stream_id": "s1",
                             "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
            time.sleep(0.1)
            # 静音 300ms → 高能量 1.2s（speech start）→ 静音 1500ms
            # （hangover 1200ms（2026-08-26 儿童语速停顿加长）→ 结束）
            voice.send_bytes(_silence(300))
            time.sleep(0.1)
            voice.send_bytes(_tone(1200))
            time.sleep(0.1)
            voice.send_bytes(_silence(1500))

            # 在语音通道保持打开的同时轮询 Realtime（asr.final 在 VAD 结束后推送）
            deadline = time.time() + 30
            got_final = False
            while time.time() < deadline and not got_final:
                try:
                    msg = json.loads(realtime.receive_text())
                except Exception:
                    break
                received.append(msg)
                if msg.get("type") == "asr.final":
                    got_final = True
                    assert msg["payload"]["text"] == "我想看汪汪队"
                    assert msg["session_id"] == session_id
            voice.send_json({"type": "voice.close", "stream_id": "s1", "reason": "user_stop"})
            assert got_final, f"应收到 asr.final，实收事件: {[m.get('type') for m in received]}"


def test_voice_unsupported_format_rejected(voice_env):
    env, _stub = voice_env
    env.bootstrap_admin()
    _d, token = env.pair_device()
    session_id = _make_session(env, token)
    from starlette.websockets import WebSocketDisconnect

    with env.client.websocket_connect(
        f"/api/v1/voice?token={token}&session_id={session_id}"
    ) as voice:
        voice.send_json({"type": "voice.open", "stream_id": "s1",
                         "format": "opus", "sample_rate": 16000, "channels": 1})
        time.sleep(0.3)
        # 服务端以 4403 关闭：后续读取应抛出 WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            voice.send_bytes(b"xx")
            voice.receive_text()


def test_asr_unavailable_voice_entry_degrades(voice_env):
    """ASR finish 失败 → conversation.state error，会话保留（§5.4）。"""
    env, stub = voice_env
    stub.fail_mode = "unreachable"
    env.bootstrap_admin()
    _d, token = env.pair_device()
    session_id = _make_session(env, token)
    with env.client.websocket_connect(
        f"/api/v1/voice?token={token}&session_id={session_id}"
    ) as voice:
        voice.send_json({"type": "voice.open", "stream_id": "s1",
                         "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
        voice.send_bytes(_silence(200))
        voice.send_bytes(_tone(1000))
        time.sleep(0.1)
        voice.send_bytes(_silence(1500))
        time.sleep(0.5)
    # Session 仍然有效
    r = env.client.get(f"/api/v1/conversations/{session_id}",
                       headers=env.device_headers(token))
    assert r.status_code == 200


def test_vad_adaptive_threshold_catches_quiet_speech(voice_env):
    """安静房间小音量语音（远场儿童）：噪声底自适应后阈值降到下限，
    旧固定 350 阈值听不到的音量（RMS≈127）也能触发并转写。"""
    env, stub = voice_env
    env.bootstrap_admin()
    _d, token = env.pair_device()
    session_id = _make_session(env, token)
    import json

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as realtime:
        with env.client.websocket_connect(
            f"/api/v1/voice?token={token}&session_id={session_id}"
        ) as voice:
            voice.send_json({"type": "voice.open", "stream_id": "s1",
                             "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
            time.sleep(0.1)
            voice.send_bytes(_silence(2000))          # 噪声底衰减（EMA）
            voice.send_bytes(_tone(1000, amp=180))     # RMS≈127 > 下限 120
            voice.send_bytes(_silence(1500))
            deadline = time.time() + 30
            got = None
            while time.time() < deadline:
                msg = json.loads(realtime.receive_text())
                if msg.get("type") == "asr.final":
                    got = msg["payload"]
                    break
            assert got is not None and got["text"] == "我想看汪汪队"


def test_vad_transient_noise_filtered(voice_env):
    """遥控器咔哒等瞬态（100ms 高能量）：低于最短有效语音 300ms →
    静默丢弃，不送 ASR、不下发 asr.final。"""
    env, stub = voice_env
    env.bootstrap_admin()
    _d, token = env.pair_device()
    session_id = _make_session(env, token)

    with env.client.websocket_connect(
        f"/api/v1/voice?token={token}&session_id={session_id}"
    ) as voice:
        voice.send_json({"type": "voice.open", "stream_id": "s1",
                         "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
        time.sleep(0.1)
        voice.send_bytes(_silence(300))
        voice.send_bytes(_tone(100))      # 瞬态
        voice.send_bytes(_silence(1500))  # hangover 后 too_short → cancel
        time.sleep(1.0)
    assert stub.finished == []            # 从未 finish（无 ASR 转写）
    assert stub.utterances == {}          # cancel 已清理
