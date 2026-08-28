"""Realtime Channel（技术方案 §4）。

单个 Device Realtime WebSocket；envelope: v/type/event_id/seq/ts/session_id?/
playback_id?/correlation_id?/payload。Hub→TV seq 对每个 Device 单调递增；
内存 ring buffer（最近 256 事件或 60 秒）用于短断线重放；窗口不足 → sync.required。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..util import new_id, now_iso

logger = logging.getLogger("kindo.realtime")

REPLAY_MAX_EVENTS = 256
REPLAY_MAX_SECONDS = 60.0


def build_envelope(
    event_type: str,
    payload: dict,
    *,
    seq: int | None = None,
    event_id: str | None = None,
    session_id: str | None = None,
    playback_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    env: dict[str, Any] = {"v": 1, "type": event_type, "ts": now_iso(), "payload": payload}
    if event_id:
        env["event_id"] = event_id
    if seq is not None:
        env["seq"] = seq
    env["session_id"] = session_id
    env["playback_id"] = playback_id
    env["correlation_id"] = correlation_id
    return env


@dataclass
class DeviceChannel:
    device_id: str
    seq: int = 0
    buffer: deque = field(default_factory=deque)  # (seq, ts, envelope_dict)
    queues: set[asyncio.Queue] = field(default_factory=set)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def push(self, envelope: dict) -> None:
        now = time.monotonic()
        self.buffer.append((envelope["seq"], now, envelope))
        while len(self.buffer) > REPLAY_MAX_EVENTS:
            self.buffer.popleft()
        while self.buffer and now - self.buffer[0][1] > REPLAY_MAX_SECONDS:
            self.buffer.popleft()

    def replay_after(self, last_server_seq: int) -> list[dict] | None:
        """返回 last_server_seq 之后的事件；窗口已缺失时返回 None（触发 sync.required）。"""
        if not self.buffer:
            return None if last_server_seq < self.seq else []
        oldest = self.buffer[0][0]
        if last_server_seq < oldest - 1:
            return None
        return [env for seq, _ts, env in self.buffer if seq > last_server_seq]


class RealtimeRegistry:
    """emit() 可从主事件循环、FastAPI 线程池与 orchestrator 工作线程并发调用。

    seq 分配与入队必须保序：threading.Lock 内完成 seq 分配与 buffer 写入；
    跨线程投递经 loop.call_soon_threadsafe 切回队列所属事件循环
    （asyncio.Queue 非线程安全），且在锁内调度以保证与 seq 一致。
    """

    def __init__(self) -> None:
        self._channels: dict[str, DeviceChannel] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """记录队列所属事件循环（lifespan 启动时绑定）。"""
        with self._lock:
            self._loop = loop

    async def subscribe(self, device_id: str) -> tuple[asyncio.Queue, DeviceChannel]:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        with self._lock:
            ch = self._channels.get(device_id)
            if ch is None:
                ch = DeviceChannel(device_id=device_id)
                self._channels[device_id] = ch
            q: asyncio.Queue = asyncio.Queue(maxsize=512)
            ch.queues.add(q)
            return q, ch

    async def unsubscribe(self, device_id: str, q: asyncio.Queue) -> None:
        self.unsubscribe_sync(device_id, q)

    def unsubscribe_sync(self, device_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            ch = self._channels.get(device_id)
            if ch is not None:
                ch.queues.discard(q)
                if not ch.queues and not ch.buffer:
                    pass  # 保留 channel 以维持 seq 连续与短重放窗口

    def is_online(self, device_id: str) -> bool:
        ch = self._channels.get(device_id)
        return bool(ch and ch.queues)

    def emit(
        self,
        device_id: str,
        event_type: str,
        payload: dict,
        *,
        session_id: str | None = None,
        playback_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        with self._lock:
            ch = self._channels.get(device_id)
            if ch is None:
                # 设备不在线也要维持 seq/buffer，使重连后可重放
                ch = DeviceChannel(device_id=device_id)
                self._channels[device_id] = ch
            envelope = build_envelope(
                event_type, payload, seq=ch.next_seq(), event_id=new_id(),
                session_id=session_id, playback_id=playback_id, correlation_id=correlation_id,
            )
            ch.push(envelope)
            self._dispatch_locked(ch, envelope)
        return envelope

    def _dispatch_locked(self, ch: DeviceChannel, envelope: dict) -> None:
        """把 envelope 投递到设备全部订阅队列；跨线程时切回队列所属循环。"""
        if not ch.queues:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._put_all(ch, envelope)
        else:
            loop.call_soon_threadsafe(self._put_all, ch, envelope)

    @staticmethod
    def _put_all(ch: DeviceChannel, envelope: dict) -> None:
        for q in list(ch.queues):
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                logger.warning("realtime 队列满，device=%s 事件丢弃（将由重连 sync 恢复）",
                               ch.device_id)

    def channel(self, device_id: str) -> DeviceChannel | None:
        return self._channels.get(device_id)

    def close_device(self, device_id: str) -> None:
        """向设备全部连接投递关闭哨兵（设备撤销等场景）。"""
        with self._lock:
            ch = self._channels.get(device_id)
            if ch is None:
                return
            loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._put_sentinels, ch)
        else:
            self._put_sentinels(ch)

    @staticmethod
    def _put_sentinels(ch: DeviceChannel) -> None:
        for q in list(ch.queues):
            try:
                q.put_nowait(None)  # None = 断开指令
            except asyncio.QueueFull:
                pass
