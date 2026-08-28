"""Realtime WebSocket（技术方案 §4）与 Voice WebSocket（技术方案 §5）。

Realtime：envelope/seq/ACK/重放（256 事件或 60s）/sync.required；空闲 30s protocol ping，
60s 无帧判死（§4.3）。Realtime 断开不结束 Conversation Session。
Voice：与 Realtime 分离（音频高频二进制流）；Hub 侧 VAD；单 utterance 30s 上限；
缓冲 >2s 发 voice.backpressure；原始音频仅内存，final 后释放，不落盘不写日志。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from array import array
from collections import deque

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..errors import KindoError
from ..pairing import authenticate_device
from ..util import new_id
from .deps import ws_device_token

logger = logging.getLogger("kindo.ws")

router = APIRouter(tags=["ws"])

PING_IDLE_SECONDS = 30
DEAD_CONNECTION_SECONDS = 60


@router.websocket("/api/v1/realtime")
async def realtime_ws(ws: WebSocket) -> None:
    state = get_state_from_ws(ws)
    token = ws_device_token(ws)
    if not token:
        await ws.close(code=4401, reason="missing device token")
        return
    session = state.db.session()
    try:
        device = authenticate_device(session, token)
    except KindoError:
        await ws.close(code=4401, reason="invalid device token")
        session.close()
        return
    except Exception:
        logger.exception("realtime 鉴权异常")
        await ws.close(code=1011, reason="internal error")
        session.close()
        return
    # subscribe 必须先于 accept：accept 与订阅之间 emit 的事件只会落入
    # buffer 且无 hello 重放兜底，将永久丢失该连接（v0.3 接力 offer 竞态修复）
    queue, channel = await state.realtime.subscribe(device.id)
    await ws.accept()
    last_frame = time.monotonic()

    async def _sender() -> None:
        while True:
            envelope = await queue.get()
            if envelope is None:  # close_device 哨兵
                await ws.close(code=1011, reason="device revoked")
                return
            await ws.send_text(json.dumps(envelope, ensure_ascii=False))

    async def _keepalive() -> None:
        nonlocal last_frame
        while True:
            await asyncio.sleep(5)
            idle = time.monotonic() - last_frame
            if idle >= DEAD_CONNECTION_SECONDS:
                await ws.close(code=1000, reason="idle timeout")
                return
            if idle >= PING_IDLE_SECONDS:
                try:
                    await ws.send_json({"v": 1, "type": "ping", "payload": {}})
                except Exception:
                    return

    sender = asyncio.create_task(_sender())
    keepalive = asyncio.create_task(_keepalive())
    try:
        while True:
            raw = await ws.receive_text()
            last_frame = time.monotonic()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type", "")
            if mtype == "hello":
                await _handle_hello(ws, channel, device.id, msg)
            elif mtype.startswith("playback."):
                kind = mtype.split(".", 1)[1]
                event = {"type": mtype, **msg}
                try:
                    result = await asyncio.to_thread(
                        state.playback.handle_tv_event, session, device, event
                    )
                    await ws.send_json({
                        "v": 1, "type": "ack", "payload": result,
                        "correlation_id": msg.get("event_id"),
                    })
                except KindoError as exc:
                    await ws.send_json({
                        "v": 1, "type": "error", "payload": {
                            "code": exc.code, "message": exc.message,
                            "reason_code": exc.reason_code,
                        },
                        "correlation_id": msg.get("event_id"),
                    })
            elif mtype in ("tts.started", "tts.finished", "tts.interrupted"):
                kind = mtype.split(".", 1)[1]
                tts_id = msg.get("tts_id", "")
                state.orchestrator.on_tts_event(device.id, kind, tts_id)
            elif mtype.startswith("transition."):
                action = mtype.split(".", 1)[1]
                tid = msg.get("transition_id", "")
                state2 = get_state_from_ws(ws)
                with state2.db.session() as session:
                    tr = state2.transition
                    if action == "select":
                        result = tr.on_select(session, device.id, tid,
                                              msg.get("option_type", ""))
                    elif action == "reject":
                        result = tr.on_reject(session, device.id, tid)
                    elif action == "activity_done":
                        result = tr.on_activity_done(session, device.id, tid)
                    else:
                        result = {"ok": False, "reason": "未知 transition 动作"}
                await ws.send_text(json.dumps({
                    "type": "ack", "correlation_id": msg.get("event_id") or tid,
                    "payload": result or {},
                }))
            elif mtype == "ui.selection":
                conv = state.conversation_manager.get_optional(msg.get("session_id", ""))
                if conv is not None and conv.device_id == device.id:
                    state.orchestrator.on_selection(conv, msg.get("option_id", ""))
            elif mtype == "ping":
                await ws.send_json({"v": 1, "type": "pong", "payload": {}})
            # 未知类型：忽略（前向兼容）
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("realtime 连接异常 device=%s", device.id)
    finally:
        sender.cancel()
        keepalive.cancel()
        for t in (sender, keepalive):
            # CancelledError 是 BaseException 子类，需与 Exception 一并列出
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        with contextlib.suppress(Exception):
            await state.realtime.unsubscribe(device.id, queue)
        session.close()


async def _handle_hello(ws: WebSocket, channel, device_id: str, msg: dict) -> None:
    last_seq = msg.get("last_server_seq")
    if last_seq is None:
        return
    replay = channel.replay_after(int(last_seq))
    if replay is None:
        await ws.send_json({"v": 1, "type": "sync.required",
                            "payload": {"reason": "replay window missed"}})
        return
    for env in replay:
        await ws.send_text(json.dumps(env, ensure_ascii=False))


def get_state_from_ws(ws: WebSocket):
    return ws.app.state.kindo


# ==================== Voice WebSocket（§5） ====================

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
FRAME_MS = 100
FRAME_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * FRAME_MS // 1000  # 3200
# ---- VAD（2026-08-26 儿童 ASR 兼容改造）----
# 阈值随噪声底自适应：安静房间/远场小音量儿童语音可触发，嘈杂环境不误触发
VAD_RMS_THRESHOLD = 350.0        # 阈值上限（沿用 v0.2 固定值，响亮环境保护）
VAD_RMS_FLOOR = 120.0            # 阈值下限（再安静也不低于此，防电路噪声）
VAD_NOISE_FACTOR = 2.5           # 阈值 = clamp(noise_floor × 2.5, 120, 350)
VAD_NOISE_INIT = 140.0           # 噪声底初值（≈350/2.5，首次行为≈旧固定阈值）
VAD_HANGOVER_MS = 1200           # 儿童语速不稳、句中停顿长：静音 1.2s 才判结束
VAD_PREROLL_MS = 500             # 预滚环形缓冲：轻声起音首字不丢
VAD_MIN_VOICED_MS = 300          # 最短有效语音：遥控器咔哒等瞬态不进 ASR
UTTERANCE_MAX_MS = 30_000        # 单 utterance 上限（§5.2）
BACKPRESSURE_BYTES = 2 * SAMPLE_RATE * BYTES_PER_SAMPLE  # 2s 音频
BACKPRESSURE_RECOVER_MS = 3_000


@router.websocket("/api/v1/voice")
async def voice_ws(ws: WebSocket, session_id: str = "") -> None:
    state = get_state_from_ws(ws)
    token = ws_device_token(ws)
    if not token:
        await ws.close(code=4401, reason="missing device token")
        return
    db = state.db.session()
    try:
        device = authenticate_device(db, token)
    except KindoError:
        await ws.close(code=4401, reason="invalid device token")
        db.close()
        return
    except Exception:
        logger.exception("voice 鉴权异常")
        await ws.close(code=1011, reason="internal error")
        db.close()
        return
    conv = state.conversation_manager.get_optional(session_id)
    if conv is None or conv.device_id != device.id:
        await ws.close(code=4400, reason="invalid session_id")
        db.close()
        return
    if not state.asr.configured:
        await ws.close(code=4503, reason="asr not configured")
        db.close()
        return

    await ws.accept()
    engine = _VoiceEngine(state, conv, device)
    try:
        while True:
            # 客户端静默挂起时 receive() 无限阻塞会泄漏 db 连接与引擎状态，
            # 用超时判死连接（§4.3：60s 无帧）
            try:
                message = await asyncio.wait_for(
                    ws.receive(), timeout=DEAD_CONNECTION_SECONDS
                )
            except TimeoutError:
                logger.info("voice 连接 %ds 无帧，判死断开 device=%s",
                            DEAD_CONNECTION_SECONDS, device.id)
                break
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            data = message.get("bytes")
            if text is not None:
                try:
                    ctrl = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if ctrl.get("type") == "voice.open":
                    ok = await engine.open(ctrl)
                    if not ok:
                        await ws.close(code=4403, reason="unsupported audio format")
                        break
                elif ctrl.get("type") == "voice.close":
                    await engine.close(ctrl.get("reason", "user_stop"))
                    break
            elif data:
                # 背压：待转发字节超阈值 → 通知 TV 暂停采集；持续 3s 未恢复 → 结束 utterance 提示重试（§5.4）
                if engine.pending_bytes > BACKPRESSURE_BYTES:
                    now_mono = time.monotonic()
                    if engine.backpressure_since is None:
                        engine.backpressure_since = now_mono
                        await ws.send_json({"v": 1, "type": "voice.backpressure", "payload": {}})
                    elif now_mono - engine.backpressure_since > BACKPRESSURE_RECOVER_MS / 1000:
                        uid = engine.utterance_id
                        await engine.finish_utterance(reason="backpressure_timeout",
                                                      suppress_final=True)
                        engine.backpressure_since = None
                        await ws.send_json({"v": 1, "type": "asr.final", "payload": {
                            "utterance_id": uid, "text": "",
                            "retry_hint": "网络有点忙，再说一次好吗",
                        }})
                    continue
                engine.backpressure_since = None
                await engine.feed(data)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice 连接异常 device=%s", device.id)
    finally:
        with contextlib.suppress(Exception):
            await engine.abort()
        db.close()


class _VoiceEngine:
    """Hub 侧 VAD + utterance 生命周期（§5.2/§5.3）。原始音频仅内存。"""

    def __init__(self, state, conv, device):
        self.state = state
        self.conv = conv
        self.device = device
        self.opened = False
        self.sample_rate = SAMPLE_RATE
        self.channels = 1
        self.buffer = bytearray()
        self.pending_bytes = 0
        self.backpressure_since: float | None = None
        self.utterance_id: str | None = None
        self.speech_started = False
        self.speech_ms = 0
        self.voiced_ms = 0
        self.silence_ms = 0
        # 噪声底估计（静默期缓慢追踪）；预滚缓冲保留最近 500ms 音频
        self.noise_floor = VAD_NOISE_INIT
        self.preroll: deque[bytes] = deque(maxlen=VAD_PREROLL_MS // FRAME_MS)
        self._asr = state.asr
        self._realtime = state.realtime
        self._orchestrator = state.orchestrator

    async def open(self, ctrl: dict) -> bool:
        if ctrl.get("format") not in ("pcm_s16le",):
            return False
        try:
            self.sample_rate = int(ctrl.get("sample_rate", SAMPLE_RATE))
            self.channels = int(ctrl.get("channels", 1))
        except (TypeError, ValueError):
            return False
        if self.sample_rate != SAMPLE_RATE or self.channels != 1:
            return False
        self.opened = True
        return True

    async def feed(self, pcm: bytes) -> None:
        if not self.opened or not pcm:
            return
        self.buffer.extend(pcm)
        self.pending_bytes += len(pcm)

        while len(self.buffer) >= FRAME_BYTES:
            frame = bytes(self.buffer[:FRAME_BYTES])
            del self.buffer[:FRAME_BYTES]
            await self._process_frame(frame)
            # 帧一旦被消费（转发或按静音丢弃）即扣减，避免纯静音期
            # pending 只增不减导致误发背压（§5.4 背压只针对在途转发积压）
            self.pending_bytes = max(0, self.pending_bytes - len(frame))

    async def _process_frame(self, frame: bytes) -> None:
        rms = _pcm_rms(frame)
        threshold = max(VAD_RMS_FLOOR, min(VAD_RMS_THRESHOLD,
                                           self.noise_floor * VAD_NOISE_FACTOR))
        speech = rms >= threshold

        if not self.speech_started:
            if not speech:
                # 噪声底自适应（仅静默期）：EMA 慢速追踪
                self.noise_floor = 0.85 * self.noise_floor + 0.15 * rms
                self.preroll.append(frame)
                return
            self.speech_started = True
            self.speech_ms = (len(self.preroll) + 1) * FRAME_MS
            self.voiced_ms = FRAME_MS
            self.silence_ms = 0
            await self._start_utterance()
            # 预滚先于触发帧转发：轻声起音首字不丢（儿童语音兼容）
            for pre in self.preroll:
                await self._forward(pre)
            self.preroll.clear()
            await self._forward(frame)
            return

        self.speech_ms += FRAME_MS
        if speech:
            self.voiced_ms += FRAME_MS
        await self._forward(frame)
        if speech:
            self.silence_ms = 0
        else:
            self.silence_ms += FRAME_MS
            if self.silence_ms >= VAD_HANGOVER_MS:
                if self.voiced_ms < VAD_MIN_VOICED_MS:
                    # 瞬态噪声（遥控器咔哒/碰撞声）不进 ASR：静默丢弃
                    await self.finish_utterance(reason="too_short", suppress_final=True)
                else:
                    await self.finish_utterance(reason="speech_end")
                return
        if self.speech_ms >= UTTERANCE_MAX_MS:
            await self.finish_utterance(reason="max_duration")

    async def _forward(self, pcm: bytes) -> None:
        if self.utterance_id is None:
            return
        try:
            await self._asr.feed(self.utterance_id, pcm)
        except Exception as exc:
            logger.warning("ASR feed 失败（结束本轮语音）: %s", exc)
            await self.finish_utterance(reason="asr_error", suppress_final=True)
            self._realtime.emit(
                self.device.id, "conversation.state",
                {"state": "error", "reason": "asr_unavailable"},
                session_id=self.conv.session_id,
            )

    async def _start_utterance(self) -> None:
        self.utterance_id = new_id()
        try:
            await self._asr.start_utterance(
                self.utterance_id, "pcm_s16le", self.sample_rate, self.channels
            )
        except Exception as exc:
            logger.warning("ASR start 失败: %s", exc)
            self.utterance_id = None
            self.speech_started = False
            return
        self._realtime.emit(
            self.device.id, "conversation.state", {"state": "transcribing"},
            session_id=self.conv.session_id,
        )

    async def finish_utterance(self, reason: str, suppress_final: bool = False) -> None:
        uid = self.utterance_id
        self.utterance_id = None
        self.speech_started = False
        self.speech_ms = 0
        self.voiced_ms = 0
        self.silence_ms = 0
        self.pending_bytes = 0
        self.preroll.clear()
        if uid is None:
            return
        if suppress_final:
            await self._asr.cancel(uid)
            return
        try:
            final = await self._asr.finish(uid)  # audio released in ASR service after final
        except Exception:
            logger.exception("ASR finish 失败")
            self._realtime.emit(
                self.device.id, "conversation.state",
                {"state": "error", "reason": "asr_error"},
                session_id=self.conv.session_id,
            )
            return
        text = final.text.strip()
        confidence = final.confidence
        # 低置信/空文本不进入状态改变（§5.3）
        if not text or (confidence is not None and confidence < 0.3):
            self._realtime.emit(
                self.device.id, "asr.final",
                {"utterance_id": uid, "text": text, "confidence": confidence,
                 "retry_hint": "我没听清，再说一次好吗？"},
                session_id=self.conv.session_id,
            )
            return
        self._realtime.emit(
            self.device.id, "asr.final",
            {"utterance_id": uid, "text": text, "confidence": confidence},
            session_id=self.conv.session_id,
        )
        self._orchestrator.on_transcript(self.conv, text)

    async def close(self, reason: str) -> None:
        if self.speech_started:
            await self.finish_utterance(reason=reason)
        else:
            await self.abort()

    async def abort(self) -> None:
        uid, self.utterance_id = self.utterance_id, None
        self.speech_started = False
        self.buffer.clear()
        if uid is not None:
            await self._asr.cancel(uid)


def _pcm_rms(frame: bytes) -> float:
    """16-bit PCM 振幅 RMS（array 切片避免逐样本 Python 循环拖慢事件循环）。"""
    samples = array("h")
    samples.frombytes(frame)
    count = len(samples)
    if count == 0:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / count)
