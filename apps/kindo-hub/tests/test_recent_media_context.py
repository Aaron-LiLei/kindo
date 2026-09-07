"""最近媒体上下文（T-20260902-003-01 / PRD AI-002/011~013、P06）。

场景：对话找片 → 播放超过会话空闲时间 → 播放中/播放后继续提问 → 再次检索或播放。
短期 Conversation Session 内存过期即释放；最近播放媒体/系列/集/主题
（Playback 表派生）与最后讨论对象（app_setting 客观引用）由新会话恢复，
对话原文不持久化（AI-013）。
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from conftest import build_sample_library, requires_ffprobe
from test_e2e_conversation import _last_tool_result, pump
from test_e2e_conversation import llm_env as scripted_llm_env  # noqa: F401 -- pytest fixture 按模块属性名再导出

# ---------- 工具 ----------

def _scan_library(env) -> None:
    """管理员触发全库扫描并等待完成（样本媒体入库）。"""
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        if env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()["state"] in ("done", "failed"):
            return
        time.sleep(0.5)


def _expire_all(env) -> int:
    """把全部活跃会话按空闲阈值判过期并 sweep（等价于真实空等超时）。"""
    mgr = env.state.conversation_manager
    for s in mgr.all_sessions():
        s.last_activity_at = datetime.now(UTC) - timedelta(
            seconds=env.state.config.session_idle_seconds + 5)
    return mgr.sweep_idle()


def _fresh_conv(env, device_id: str):
    from kindo.conversation.service import ConversationSession

    return ConversationSession(
        session_id="fresh-ctx", device_id=device_id, profile_id="default",
        provider_id="p", model_id="m")


def _context_block(env, device_id: str, user_text: str = "再看一集") -> str:
    from kindo.conversation.context import build_context_block

    with env.db.session() as db:
        return build_context_block(
            db, _fresh_conv(env, device_id), "default",
            env.state.playback, env.state.policy, env.state.history, user_text)


def _first_media(env, token: str, title_part: str) -> dict:
    items = env.client.get("/api/v1/media",
                           headers=env.device_headers(token)).json()["items"]
    return next(i for i in items if title_part in i["title"])


def _play(env, headers: dict, media_id: str) -> str:
    r = env.client.post("/api/v1/playbacks", json={
        "media_id": media_id, "action": "play", "source": "ui"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["playback_id"]


def _watch(env, token: str, playback_id: str, position_ms: int, *, end: bool) -> None:
    """经 WS 上报播放进度（playing）；end=True 时按该进度收尾（完成判定同流内）。"""
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": f"s{time.monotonic_ns()}",
                      "playback_id": playback_id, "position_ms": 0})
        kind = "playback.ended" if end else "playback.progress"
        ws.send_json({"type": kind, "event_id": f"p{time.monotonic_ns()}",
                      "playback_id": playback_id, "position_ms": position_ms})
        time.sleep(0.4)


# ---------- 单元：最近讨论对象提取 ----------

def test_extract_discussed_precedence_and_privacy():
    from kindo.conversation.recent import _extract  # noqa: SLF001

    class _Conv:
        recent_tool_results: list = []

    # 无引用
    assert _extract(_Conv()) is None
    # 后发生的结果优先（search → play 取 play）
    _Conv.recent_tool_results = [
        {"tool": "search_media", "status": "clarify",
         "data": {"candidates": [{"media_id": "m1", "title": "A 第一集"}]}},
        {"tool": "play_media", "status": "ok",
         "data": {"playback_id": "pb1", "media_id": "m2", "title": "A 第二集"}},
    ]
    assert _extract(_Conv()) == {"media_id": "m2", "title": "A 第二集"}
    # 未播放只有候选：取候选首位
    _Conv.recent_tool_results = [
        {"tool": "search_media", "status": "clarify",
         "data": {"candidates": [{"media_id": "m1", "label": "A 第一集"}]}},
    ]
    assert _extract(_Conv()) == {"media_id": "m1", "title": "A 第一集"}
    # read_story：只留标题，故事原文不进入存储（AI-013/硬性约束 14）
    _Conv.recent_tool_results = [
        {"tool": "read_story", "status": "ok",
         "data": {"direct_speak": True, "title": "三只小猪", "speak_text": "很久很久以前……"}},
    ]
    ref = _extract(_Conv())
    assert ref == {"title": "三只小猪"}
    assert "很久" not in str(ref)


# ---------- 播放结束后：新会话恢复最近播放 ----------

@requires_ffprobe
@pytest.mark.slow
def test_recent_playback_restored_after_session_expiry(env):
    build_sample_library(env.media_dir)
    _scan_library(env)
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    ep1 = _first_media(env, token, "第1集")
    ep2 = _first_media(env, token, "第2集")

    # 看完第 1 集（95% → 完成判定）
    pb_id = _play(env, headers, ep1["media_id"])
    _watch(env, token, pb_id, int(ep1["duration_ms"] * 0.95), end=True)

    # 会话过期（超过空闲阈值 sweep 收口）；无 Provider 时直建会话即可
    env.state.conversation_manager.create(device_id, "default", "p", "m", None)
    assert _expire_all(env) == 1

    block = _context_block(env, device_id)
    assert "最近媒体上下文" in block
    assert f"最近播放: 《{ep1['title']}》 — 已看完" in block
    assert "系列: 《汪汪队立大功》第1季第1集（该系列共2集）" in block
    assert f"下一集《{ep2['title']}》(media_id={ep2['media_id']})" in block
    assert "主题: 救援" in block
    # 会话原文不回来（AI-013）：恢复的只有客观引用
    assert "user_input" not in block


@requires_ffprobe
@pytest.mark.slow
def test_recent_context_window_expires_after_48h(env):
    build_sample_library(env.media_dir)
    _scan_library(env)
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    ep1 = _first_media(env, token, "第1集")
    pb_id = _play(env, headers, ep1["media_id"])
    _watch(env, token, pb_id, int(ep1["duration_ms"] * 0.95), end=True)

    from kindo.models import Playback

    with env.db.session() as db:
        for p in db.query(Playback).all():
            p.created_at = datetime.now(UTC) - timedelta(hours=49)
        db.commit()
    assert "最近媒体上下文" not in _context_block(env, device_id)


# ---------- 播放中：当前播放块带系列/下一集锚点 ----------

@requires_ffprobe
@pytest.mark.slow
def test_current_playback_block_has_series_anchor(env):
    build_sample_library(env.media_dir)
    _scan_library(env)
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    ep1 = _first_media(env, token, "第1集")
    ep2 = _first_media(env, token, "第2集")

    pb_id = _play(env, headers, ep1["media_id"])
    _watch(env, token, pb_id, 4000, end=False)  # 播放中（8s 样本的 4s 处）

    block = _context_block(env, device_id)
    assert "【当前播放】" in block
    assert "系列: 《汪汪队立大功》第1季第1集（该系列共2集）" in block
    assert f"下一集《{ep2['title']}》(media_id={ep2['media_id']})" in block
    # 当前播放即最近播放：去重不重复出块
    assert "最近媒体上下文" not in block


# ---------- 会话结束/过期：最近讨论对象沉淀与恢复 ----------

@requires_ffprobe
@pytest.mark.slow
def test_discussed_object_survives_session_end_without_playback(env):
    build_sample_library(env.media_dir)
    _scan_library(env)
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    ep1 = _first_media(env, token, "第1集")

    # 会话内只检索未播放（孩子还没选），会话被结束
    conv = env.state.conversation_manager.create(device_id, "default", "p", "m", None)
    with env.db.session() as db:
        from kindo.models import Device

        device = db.query(Device).first()
    result = env.state.orchestrator._tools.execute(  # noqa: SLF001
        conv, device, "default", "search_media", {"query": "汪汪队"}, "call-1")
    assert result["status"] == "clarify"
    env.client.post(f"/api/v1/conversations/{conv.session_id}/end", headers=headers)

    block = _context_block(env, device_id)
    assert "最近聊过" in block
    assert ep1["title"] in block  # 候选首位标题（经 media_id 解析为现行标题）


@requires_ffprobe
@pytest.mark.slow
def test_discussed_object_captured_on_idle_expiry(env):
    build_sample_library(env.media_dir)
    _scan_library(env)
    device_id, token = env.pair_device()

    conv = env.state.conversation_manager.create(device_id, "default", "p", "m", None)
    with env.db.session() as db:
        from kindo.models import Device

        device = db.query(Device).first()
    env.state.orchestrator._tools.execute(  # noqa: SLF001
        conv, device, "default", "search_media", {"query": "海底小纵队"}, "call-1")
    assert _expire_all(env) == 1  # 过期路径同样触发沉淀钩子

    assert "最近聊过" in _context_block(env, device_id)


# ---------- 端到端：找片→播放超时会话→播放中/后提问→再次播放 ----------

@requires_ffprobe
@pytest.mark.slow
def test_find_play_ask_after_expiry_then_next_episode(scripted_llm_env):  # noqa: F811 -- pytest fixture 参数
    env, llm = scripted_llm_env
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    ep1 = _first_media(env, token, "第1集")
    ep2 = _first_media(env, token, "第2集")

    def _played(body: dict) -> bool:
        return any(
            m.get("role") == "tool" and '"playback_id"' in str(m.get("content", ""))
            for m in body.get("messages", []))

    # ① 对话找片 → 播放第 1 集（真实编排闭环）
    def finder(body: dict) -> list[dict]:
        last = _last_tool_result(body)
        if last is None:
            return [llm.tool_call("search_media", {"query": "汪汪队"}, tid="t1"),
                    llm.text("让我找找", finish="tool_calls")]
        if last.get("data", {}).get("candidates") and not _played(body):
            return [llm.tool_call("play_media",
                                  {"media_id": ep1["media_id"], "action": "play"}, tid="t2"),
                    llm.text("", finish="tool_calls")]
        return [llm.text("开始啦！")]

    llm.resolver = finder
    r = env.client.post("/api/v1/conversations", json={}, headers=headers)
    conv_a = env.state.conversation_manager.get(r.json()["session_id"])

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "hello", "last_server_seq": 0})
        env.state.orchestrator.on_transcript(conv_a, "我想看汪汪队")
        pump(ws, stop_type="assistant.text.final", timeout_s=20)
        cur = env.client.get("/api/v1/playbacks/current", headers=headers).json()
        assert cur["playback"]["media_id"] == ep1["media_id"]

        # ② 播放超过会话空闲时间：会话过期收口（20 分钟空等的等价构造）
        assert _expire_all(env) == 1

        # ③ 播放中继续提问（新会话）：上下文必须恢复当前播放与系列锚点
        llm.resolver = lambda body: [llm.text("因为她有翅膀呀！")]
        r = env.client.post("/api/v1/conversations", json={}, headers=headers)
        conv_b = env.state.conversation_manager.get(r.json()["session_id"])
        env.state.orchestrator.on_transcript(conv_b, "她为什么会飞")
        pump(ws, stop_type="assistant.text.final", timeout_s=15)
        mid_play_ctx = "\n".join(
            str(m.get("content", "")) for req in llm.requests
            for m in req.get("messages", []) if m.get("role") == "system")
        assert "【当前播放】" in mid_play_ctx
        assert "系列: 《汪汪队立大功》第1季第1集（该系列共2集）" in mid_play_ctx

        # ④ 看完第 1 集 → 播放后"再看一集"（新会话）：恢复最近播放 + 直接播下一集
        pb_id = cur["playback"]["playback_id"]
        ws.send_json({"type": "playback.ended", "event_id": "done-1",
                      "playback_id": pb_id,
                      "position_ms": int(ep1["duration_ms"] * 0.95)})
        time.sleep(0.4)

        def next_ep(body: dict) -> list[dict]:
            if _last_tool_result(body) is None:
                return [llm.tool_call("play_media",
                                      {"media_id": ep2["media_id"], "action": "play"}, tid="t3"),
                        llm.text("", finish="tool_calls")]
            return [llm.text("好呀，看下一集！")]

        llm.resolver = next_ep
        r = env.client.post("/api/v1/conversations", json={}, headers=headers)
        conv_c = env.state.conversation_manager.get(r.json()["session_id"])
        env.state.orchestrator.on_transcript(conv_c, "再看一集")
        pump(ws, stop_type="assistant.text.final", timeout_s=15)
        after_ctx = "\n".join(
            str(m.get("content", "")) for req in llm.requests
            for m in req.get("messages", []) if m.get("role") == "system")
        assert "最近播放" in after_ctx and "已看完" in after_ctx
        assert f"下一集《{ep2['title']}》" in after_ctx

    cur = env.client.get("/api/v1/playbacks/current", headers=headers).json()
    assert cur["playback"]["media_id"] == ep2["media_id"]
