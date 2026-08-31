"""Conversation Orchestrator：一轮对话的 LLM 流式调用、Tool 执行与事件推送。

状态机映射（交互 v0.2 §5）：thinking → tool_running → speaking → follow_up。
失败语义（§6.4）：同 Provider 无副作用时安全重试 1 次；Tool 副作用后 LLM 失败则
按幂等缓存读取已完成结果再试一次，仍失败则进入可提示错误（不重复执行写 Tool）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from datetime import UTC

from ..agent.tools import TOOL_SCHEMAS, ToolRuntime
from ..config import Config
from ..providers.llm import (
    LlmEvent,
    OpenAIChatCompletionsAdapter,
    accumulate_tool_calls,
    with_first_event_timeout,
)
from ..providers.tts import TtsService
from ..realtime.registry import RealtimeRegistry
from ..util import new_id
from .context import build_context_block, build_messages
from .service import (
    MAX_LLM_TOOL_ROUNDS,
    STATE_ACTIVE,
    ConversationManager,
    ConversationSession,
    Turn,
)

logger = logging.getLogger("kindo.orchestrator")

# 分句流式 TTS：LLM 流式输出按句切分逐句下发 tts.request，孩子先听为快。
# 句读集合含换行；无句读超长时强制切分（防超长utterance卡住播报）。
_SENTENCE_END_CHARS = "。！？!?；;\n"
_TTS_MAX_BUFFER_CHARS = 100


def split_sentences(text: str, max_chars: int = _TTS_MAX_BUFFER_CHARS) -> list[str]:
    """整段文本 → 句列表（read_story 直接播报用；与流式增量切分同规则：
    句读即切、无句读超长强制切分）。"""
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENTENCE_END_CHARS or len(buf) >= max_chars:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        manager: ConversationManager,
        tools: ToolRuntime,
        llm: OpenAIChatCompletionsAdapter,
        tts: TtsService,
        realtime: RealtimeRegistry,
        db_session_factory,
        playback,
        policy,
        history,
        provider_resolver,
    ):
        self._cfg = cfg
        self._manager = manager
        self._tools = tools
        self._llm = llm
        self._tts = tts
        self._realtime = realtime
        self._db = db_session_factory
        self._playback = playback
        self._policy = policy
        self._history = history
        self._provider_resolver = provider_resolver  # (provider_id) -> LLMProviderConfig
        # 专用工作循环：编排可从同步（REST/测试）与异步（WS）上下文提交，
        # 同时把 LLM 流式调用与 WS 处理线程解耦
        self._loop = asyncio.new_event_loop()
        self._worker = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="kindo-orchestrator"
        )
        self._worker.start()
        # per-session Turn 串行化：同一会话连续说话时按序编排，
        # 避免两个 Turn 并发写 turns[-1] 造成错位（锁只在编排循环上使用）
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    def _submit(self, coro) -> None:
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        fut.add_done_callback(self._on_task_done)

    def submit(self, coro) -> None:
        """公共提交入口：其他模块（如 Transition 开场白生成）把异步工作
        放到本编排循环执行——TtsService 的克隆 client 绑定此循环。"""
        self._submit(coro)

    @staticmethod
    def _on_task_done(fut) -> None:
        try:
            fut.result()
        except concurrent.futures.CancelledError:
            pass
        except Exception:
            logger.exception("编排任务异常退出")

    def _turn_lock(self, conv: ConversationSession) -> asyncio.Lock:
        lock = self._turn_locks.get(conv.session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_locks[conv.session_id] = lock
        return lock

    def shutdown(self, timeout: float = 5.0) -> None:
        """停止编排循环（lifespan 收尾调用）：取消在途任务并关闭线程。"""
        if self._closed:
            return
        self._closed = True

        async def _cancel_all() -> None:
            for t in asyncio.all_tasks(self._loop):
                if t is not asyncio.current_task():
                    t.cancel()

        try:
            asyncio.run_coroutine_threadsafe(_cancel_all(), self._loop).result(timeout)
        except Exception:
            logger.warning("编排任务取消超时，继续关闭")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._worker.join(timeout=timeout)

    # ---------- 入口 ----------

    def on_transcript(self, conv: ConversationSession, text: str) -> None:
        """asr.final 进入：绑定新 Turn 并启动本轮编排。"""
        from datetime import datetime

        turn_no = (conv.turns[-1].turn_no + 1) if conv.turns else 1
        turn = Turn(turn_no=turn_no, user_input=text, created_at=datetime.now(UTC))
        conv.turns.append(turn)
        conv.touch()
        self._emit_state(conv, "thinking")
        self._submit(self._run_turn(conv, turn, text))

    def on_selection(self, conv: ConversationSession, option_id: str) -> None:
        cand = conv.candidates.get(option_id)
        if cand is None:
            logger.warning("ui.selection 未知 option_id=%s", option_id)
            return
        text = f"（孩子选择了：{cand['label']}）"
        conv.register_candidates([], source_tool="selection")
        self.on_transcript(conv, text)

    def on_tts_event(self, device_id: str, event_kind: str, tts_id: str) -> None:
        """TV 上报的 tts.started/finished/interrupted（§4.1）——驱动 FOLLOW_UP 与恢复。"""
        import time

        for s in self._manager.all_sessions():
            if s.state != STATE_ACTIVE or tts_id not in s.tts_to_session:
                continue
            if event_kind == "finished":
                if tts_id != s.last_tts_id:
                    return  # 分句流式：中间句完成不开启追问窗口
                s.follow_up_deadline = time.monotonic() + self._cfg.follow_up_seconds
                self._emit_state(s, "follow_up")
                self._submit(self._follow_up_timer(s))
            elif event_kind == "interrupted":
                s.follow_up_deadline = None
                self._emit_state(s, "listening")
            return

    async def _follow_up_timer(self, conv: ConversationSession) -> None:
        import time

        deadline = conv.follow_up_deadline
        if deadline is None:
            return
        await asyncio.sleep(max(0.1, deadline - time.monotonic()))
        if conv.follow_up_deadline == deadline and conv.state == "active":
            conv.follow_up_deadline = None  # 追问窗口结束：UI 收起（Session 继续有效）

    # ---------- 一轮编排 ----------

    async def _run_turn(self, conv: ConversationSession, turn: Turn, user_text: str) -> None:
        provider = self._provider_resolver(conv.provider_id)
        if provider is None:
            self._fail(conv, turn, "ai_unavailable", "AI 暂时不能说话")
            return
        async with self._turn_lock(conv):
            try:
                await self._run_turn_once(conv, turn, user_text, provider, allow_retry=True)
            except Exception:
                logger.exception("编排失败 session=%s", conv.session_id)
                self._fail(conv, turn, "error", "AI 暂时说不出话，我们稍后再试好不好？")

    def _build_context_sync(self, conv: ConversationSession, user_text: str) -> str:
        with self._db() as db:
            return build_context_block(
                db, conv, conv.profile_id, self._playback, self._policy, self._history, user_text
            )

    async def _run_turn_once(self, conv: ConversationSession, turn: Turn, user_text: str,
                             provider, allow_retry: bool) -> None:
        # 上下文构建含多次 DB 查询（grounding/历史/policy），放线程池避免拖慢编排循环
        context_block = await asyncio.to_thread(self._build_context_sync, conv, user_text)
        messages = build_messages(conv, context_block)

        had_side_effect = False
        emitted_text = False
        speak_buffer = ""  # 已流式到达但尚未成句的待播文本

        async def _flush_speak() -> None:
            """把未成句的余量立即下发（回合切换/流结束时的尾句）。"""
            nonlocal speak_buffer
            text = speak_buffer.strip()
            speak_buffer = ""
            if text:
                await self._speak(conv, text)

        for _round in range(MAX_LLM_TOOL_ROUNDS + 1):
            request_id = new_id()
            text_parts: list[str] = []
            events: list[LlmEvent] = []
            stream_error: str | None = None
            try:
                agen = self._llm.generate(
                    provider, messages, TOOL_SCHEMAS, request_id,
                )
                async for ev in with_first_event_timeout(agen, self._cfg.llm_first_event_timeout):
                    if ev.type == "text_delta" and ev.text:
                        text_parts.append(ev.text)
                        emitted_text = True
                        self._realtime.emit(
                            conv.device_id, "assistant.text.delta",
                            {"delta": ev.text}, session_id=conv.session_id,
                            correlation_id=request_id,
                        )
                        # 分句流式：句读到达即下发该句
                        speak_buffer += ev.text
                        while True:
                            idx = -1
                            for ch in _SENTENCE_END_CHARS:
                                pos = speak_buffer.find(ch)
                                if pos != -1 and (idx == -1 or pos < idx):
                                    idx = pos
                            if idx == -1:
                                break
                            sentence, speak_buffer = speak_buffer[:idx + 1], speak_buffer[idx + 1:]
                            if sentence.strip():
                                await self._speak(conv, sentence.strip())
                        if len(speak_buffer) >= _TTS_MAX_BUFFER_CHARS:
                            await _flush_speak()
                    elif ev.type == "tool_call_delta":
                        events.append(ev)
                    elif ev.type == "error":
                        stream_error = ev.error or "llm_error"
                        break
            except Exception as exc:
                stream_error = str(exc)[:200]

            if stream_error is not None:
                # §6.4：无副作用且未输出时，同 Provider 安全重试 1 次
                if allow_retry and not had_side_effect and not emitted_text:
                    logger.warning("LLM 流失败（将重试1次）: %s", stream_error)
                    await asyncio.sleep(0.3)
                    await self._run_turn_once(conv, turn, user_text, provider, allow_retry=False)
                    return
                self._fail(conv, turn, "provider_error", "AI 暂时说不出话，我们稍后再试好不好？")
                return

            tool_calls = accumulate_tool_calls(events)
            assistant_text = "".join(text_parts)

            if tool_calls and _round < MAX_LLM_TOOL_ROUNDS:
                # 工具轮的过渡文本立即播报（"让我找找…"），随后执行工具
                await _flush_speak()
                if assistant_text:
                    messages.append({"role": "assistant", "content": assistant_text,
                                     "tool_calls": _openai_tool_calls(tool_calls)})
                else:
                    messages.append({"role": "assistant", "content": None,
                                     "tool_calls": _openai_tool_calls(tool_calls)})
                for call in tool_calls:
                    self._emit_state(conv, "tool_running", tool=self._tools.child_friendly_status(call.name))
                    self._realtime.emit(
                        conv.device_id, "tool.started",
                        {"tool_name": call.name,
                         "child_friendly_status": self._tools.child_friendly_status(call.name)},
                        session_id=conv.session_id, correlation_id=call.id,
                    )
                    result = await self._execute_tool(conv, call)
                    if call.name == "play_media" and result.get("status") == "ok":
                        had_side_effect = True
                    self._realtime.emit(
                        conv.device_id, "tool.completed",
                        {"tool_name": call.name, "status": result.get("status")},
                        session_id=conv.session_id, correlation_id=call.id,
                    )
                    turn.tool_calls.append({"name": call.name, "id": call.id})
                    if result.get("data", {}).get("direct_speak"):
                        # 朗读型结果（read_story，§7.4）：原文直接分句播报，不经
                        # LLM 复述；上下文只留标题（原文不回传模型）
                        title = result["data"].get("title") or "故事"
                        await self._speak_story(conv, result["data"]["speak_text"])
                        messages.append({
                            "role": "tool", "tool_call_id": call.id,
                            "content": json.dumps({"status": "ok", "data": {
                                "read": True, "title": title}}, ensure_ascii=False),
                        })
                        messages.append({
                            "role": "assistant",
                            "content": f"（已用家里的声音朗读了故事《{title}》）",
                        })
                        turn.assistant_output = f"（朗读了故事：《{title}》）"
                        conv.touch()
                        return
                    messages.append({
                        "role": "tool", "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                continue  # 把 Tool 结果送回 LLM

            # 最终回复：尾句先于 assistant.text.final 下发（TV 以 final 作为
            # "本回合 tts.request 已发完"的信号驱动追问窗口）
            final = assistant_text or "（AI 没有说话）"
            turn.assistant_output = final
            conv.touch()
            await _flush_speak()
            if not assistant_text:
                await self._speak(conv, final)
            self._realtime.emit(
                conv.device_id, "assistant.text.final",
                {"text": final, "response_id": request_id},
                session_id=conv.session_id, correlation_id=request_id,
            )
            return

        self._fail(conv, turn, "error", "这个问题有点难，我们换个说法试试好吗？")

    async def _execute_tool(self, conv: ConversationSession, call) -> dict:
        import asyncio

        try:
            raw_args = json.loads(call.arguments) if call.arguments.strip() else {}
        except json.JSONDecodeError:
            return {"status": "error", "data": {}, "reason_code": None,
                    "constraints": {}, "message_hint": "工具参数不是合法 JSON"}
        if not isinstance(raw_args, dict):
            raw_args = {}
        # 短开短关：读完 Device 即释放 session，不在 await 期间持有连接
        with self._db() as db:
            from ..models import Device

            device = db.query(Device).filter(Device.id == conv.device_id).first()
        if device is None:
            return {"status": "error", "data": {}, "reason_code": None,
                    "constraints": {}, "message_hint": "设备不存在"}
        # Tool 在线程池执行（内部为同步 DB/子进程操作，自开 session）
        return await asyncio.to_thread(
            self._tools.execute, conv, device, conv.profile_id,
            call.name, raw_args, call.id,
        )

    async def _speak(self, conv: ConversationSession, text: str) -> None:
        """tts.request 下发 TV Android TTS 执行；由 tts.* 事件驱动 FOLLOW_UP（§11.4）。

        分句流式：一回合并发多条 tts.request，conv.last_tts_id 记录末句——
        TV 逐句回报 tts.finished，仅末句完成才开启追问窗口。"""
        if conv.state != STATE_ACTIVE:
            # 会话已被家长/孩子结束（不聊了/端点关闭）：迟到的响应不再下发，
            # 避免 TTS 盖在 TV 已开始的播放上
            return
        tts_id = new_id()
        conv.tts_to_session[tts_id] = conv.session_id
        conv.last_tts_id = tts_id
        # §6.7：hub_tts（家长声音克隆，可选）优先按句合成；失败/冷却/未配置回退 android_tts
        instruction = await self._tts.render(tts_id, text)
        self._emit_state(conv, "speaking")
        self._realtime.emit(
            conv.device_id, "tts.request", instruction.to_payload(),
            session_id=conv.session_id, correlation_id=tts_id,
        )

    async def _speak_story(self, conv: ConversationSession, text: str) -> None:
        """故事原文分句播报（read_story direct_speak 路径，§6.6 同机制）：
        逐句 assistant.text.delta + tts.request，末句 last_tts_id 驱动追问窗口。
        原文只走 TTS，不进入模型上下文。"""
        request_id = new_id()
        sentences = split_sentences(text)
        self._emit_state(conv, "speaking")
        full = ""
        for s in sentences:
            full += s
            self._realtime.emit(
                conv.device_id, "assistant.text.delta",
                {"delta": s}, session_id=conv.session_id,
                correlation_id=request_id,
            )
            await self._speak(conv, s)
        self._realtime.emit(
            conv.device_id, "assistant.text.final",
            {"text": full, "response_id": request_id},
            session_id=conv.session_id, correlation_id=request_id,
        )

    def _emit_state(self, conv: ConversationSession, state: str, **extra) -> None:
        payload: dict = {"state": state}
        payload.update(extra)
        self._realtime.emit(
            conv.device_id, "conversation.state", payload, session_id=conv.session_id
        )

    def _fail(self, conv: ConversationSession, turn: Turn, reason: str, child_message: str) -> None:
        self._emit_state(conv, "error", reason=reason)
        turn.assistant_output = child_message
        conv.touch()
        self._realtime.emit(
            conv.device_id, "assistant.text.final",
            {"text": child_message, "response_id": new_id(), "fallback": True},
            session_id=conv.session_id,
        )


def _openai_tool_calls(tool_calls) -> list[dict]:
    return [
        {
            "id": c.id,
            "type": "function",
            "function": {"name": c.name, "arguments": c.arguments},
        }
        for c in tool_calls
    ]
