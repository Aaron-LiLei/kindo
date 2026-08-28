"""AI_VOICE 计量闭环回归（2026-08-26 工程治理）。

覆盖：conversation_usage 三条结束路径（显式 end / 空闲 sweep / 重启孤儿收尾）、
ai_voice 预算聚合含常规对话、判定矩阵 AI_VOICE 分支（预算尽拒新对话、
resume 不设门、预算 null 不拦）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kindo.config import LLMProviderConfig
from kindo.models import ConversationUsage, TransitionSession


def _inject_provider(env) -> None:
    """语音测试不真正调 LLM：注入指向不存在地址的 Provider 满足可用性检查。"""
    env.state.config.llm_providers = [LLMProviderConfig({
        "id": "main", "display_name": "x", "protocol": "openai_chat_completions",
        "base_url": "http://127.0.0.1:19998/v1", "model": "m",
    })]
    env.state.provider_registry.reload()


def _new_conversation(env, token) -> str:
    r = env.client.post("/api/v1/conversations", json={},
                        headers=env.device_headers(token))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _usage_row(env, session_id) -> ConversationUsage | None:
    with env.db.session() as s:
        return s.query(ConversationUsage).filter(
            ConversationUsage.session_id == session_id).one_or_none()


def test_conversation_usage_recorded_on_end(env):
    env.bootstrap_admin()
    _inject_provider(env)
    _d, token = env.pair_device()
    sid = _new_conversation(env, token)
    row = _usage_row(env, sid)
    assert row is not None and row.ended_at is None  # 创建即落行

    s = env.state.conversation_manager.get_optional(sid)
    s.created_at = datetime.now(UTC) - timedelta(seconds=6)
    s.touch()
    r = env.client.post(f"/api/v1/conversations/{sid}/end", json={},
                        headers=env.device_headers(token))
    assert r.status_code == 200
    row = _usage_row(env, sid)
    assert row.ended_at is not None
    assert 5000 <= row.duration_ms <= 7000  # created_at → last_activity 口径


def test_sweep_idle_records_usage(env):
    env.bootstrap_admin()
    _inject_provider(env)
    _d, token = env.pair_device()
    sid = _new_conversation(env, token)
    s = env.state.conversation_manager.get_optional(sid)
    now = datetime.now(UTC)
    s.created_at = now - timedelta(seconds=700)
    s.last_activity_at = now - timedelta(seconds=650)  # 空闲 650s > 600s
    n = env.state.conversation_manager.sweep_idle()
    assert n == 1
    row = _usage_row(env, sid)
    assert row.ended_at is not None
    assert 45_000 <= row.duration_ms <= 55_000  # 700-650=50s 互动段，空闲不计


def test_finalize_orphans_after_restart(env):
    with env.db.session() as s:
        s.add(ConversationUsage(session_id="crashed", profile_id="default",
                                device_id="d", started_at=datetime.now(UTC) - timedelta(seconds=60)))
        s.commit()
    from kindo.conversation.usage import ConversationUsageService

    n = ConversationUsageService(env.db.session_factory).finalize_orphans(600)
    assert n == 1
    row = _usage_row(env, "crashed")
    assert row.ended_at is not None and 59_000 <= row.duration_ms <= 61_000


def test_ai_voice_budget_denies_new_conversation_but_not_resume(env):
    env.bootstrap_admin()
    _inject_provider(env)
    _d, token = env.pair_device()
    sid = _new_conversation(env, token)  # 预算未配置（默认 null）→ 允许

    with env.db.session() as s:
        env.state.policy.save(s, {"budgets": {"ai_voice_minutes": 1}})
        s.add(ConversationUsage(session_id="used-1", profile_id="default", device_id="d",
                                started_at=datetime.now(UTC), ended_at=datetime.now(UTC),
                                duration_ms=60_000))  # 当日已用满 1 分钟
        s.commit()

    r = env.client.post("/api/v1/conversations", json={},
                        headers=env.device_headers(token))
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["error"]["code"] == "policy_denied"
    assert body["reason_code"] == "daily_limit_reached"
    assert body["constraints"]["modality"] == "AI_VOICE"

    # resume 进行中会话不受影响（软限制不切断）
    r = env.client.post("/api/v1/conversations",
                        json={"resume_session_id": sid},
                        headers=env.device_headers(token))
    assert r.status_code == 200

    # 预算取消（null）→ 恢复允许
    with env.db.session() as s:
        env.state.policy.save(s, {"budgets": {"ai_voice_minutes": None}})
        s.commit()
    r = env.client.post("/api/v1/conversations", json={},
                        headers=env.device_headers(token))
    assert r.status_code == 200


def test_ai_voice_consumed_sums_transition_and_conversation(env):
    now = datetime.now(UTC)
    with env.db.session() as s:
        s.add(TransitionSession(id="t1", profile_id="default",
                                trigger_key="k-ai-voice-1", ai_voice_ms=5_000,
                                created_at=now))
        s.add(ConversationUsage(session_id="c1", profile_id="default", device_id="d",
                                started_at=now, duration_ms=7_000))
        s.commit()
    with env.db.session() as s:
        total = env.state.policy.ai_voice_consumed_ms(s, "default", now)
    assert total == 12_000
