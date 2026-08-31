"""AI Runtime 单测（技术方案 §19.1；AC-19 权限隔离）。

不触网：LLM 调用经 FakeAdapter / 内存库验证 run_ai 的解析-重试-失败语义、
上下文白名单（§19.6 数据最小化）与家长/儿童 Tool 注册表隔离。
"""
from __future__ import annotations

import inspect
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kindo.ai import tools as ai_tools
from kindo.ai.context import build_curator_batch_context
from kindo.ai.profiles import get_profile
from kindo.ai.runtime import AiRuntimeError, LLMRuntime, parse_model_json, validate_output
from kindo.models import Base, ContentEntity, MediaAsset


class FakeView:
    id = "p1"
    model = "test-model"
    base_url = "http://localhost:9"
    api_key = ""
    enabled = True


class FakeRegistry:
    def all(self):
        return [FakeView()]

    def get(self, pid):
        return FakeView() if pid == "p1" else None


def _memory_runtime(adapter) -> LLMRuntime:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return LLMRuntime(adapter, factory, FakeRegistry())


# ---------- parse / validate ----------

def test_parse_model_json_strips_fences():
    text = '```json\n{"findings": [], "suggestions": []}\n```'
    assert parse_model_json(text) == {"findings": [], "suggestions": []}


def test_parse_model_json_garbage_raises():
    with pytest.raises(AiRuntimeError):
        parse_model_json("这不是 JSON")


def _schema():
    return get_profile("library_curator").output_schema


def test_validate_output_rejects_missing_summary_parts():
    parsed = {
        "findings": [],
        "suggestions": [{
            "entity_id": "e1", "change_type": "add_topic",
            "changes": {"names": ["海洋"]},
            "summary": {"why": "缺主题", "what": "", "impact": "更易找到"},
        }],
    }
    with pytest.raises(AiRuntimeError):
        validate_output(parsed, _schema())


def test_validate_output_rejects_bad_change_type():
    parsed = {
        "findings": [],
        "suggestions": [{
            "entity_id": "e1", "change_type": "drop_database",
            "changes": {}, "summary": {"why": "a", "what": "b", "impact": "c"},
        }],
    }
    with pytest.raises(AiRuntimeError):
        validate_output(parsed, _schema())


def test_validate_output_accepts_valid():
    parsed = {
        "findings": [{"entity_id": "e1", "issue": "疑似重复"}],
        "suggestions": [{
            "entity_id": "e1", "change_type": "add_topic",
            "changes": {"names": ["海洋"]},
            "summary": {"why": "缺主题", "what": "补充海洋主题", "impact": "更容易找到"},
        }],
    }
    validate_output(parsed, _schema())


# ---------- run_ai：解析失败重试一次，再失败抛错 ----------

class FakeAdapter:
    """与真实 OpenAIChatCompletionsAdapter 同形：generate 为 async generator。"""

    def __init__(self, replies: list[str]):
        self._replies = replies
        self.calls = 0

    async def generate(self, provider, messages, tools, request_id):
        from kindo.providers.llm import LlmEvent

        self.calls += 1
        text = self._replies[min(self.calls - 1, len(self._replies) - 1)]
        for chunk in text:
            yield LlmEvent(type="text_delta", text=chunk)
        yield LlmEvent(type="completed", finish_reason="stop")


def _run_with(replies):
    rt = _memory_runtime(FakeAdapter(replies))
    return rt, rt.run_ai(get_profile("library_curator"), '{"entities": []}')


def test_run_ai_retry_then_success():
    rt, out = _run_with([
        "模型今天不想输出 JSON",
        '{"findings": [], "suggestions": []}',
    ])
    assert out == {"findings": [], "suggestions": []}


def test_run_ai_fail_after_retry():
    with pytest.raises(AiRuntimeError):
        _run_with(["坏输出", "还是很坏"])


def test_run_ai_unready_provider():
    class EmptyRegistry(FakeRegistry):
        def all(self):
            return []

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    rt = LLMRuntime(FakeAdapter([]), sessionmaker(bind=engine), EmptyRegistry())
    assert rt.ready() is False
    with pytest.raises(AiRuntimeError):
        rt.run_ai(get_profile("library_curator"), "{}")


# ---------- Context Builder：白名单（§19.6）+ Tool Permission ----------

def _entity(session, **kw):
    e = ContentEntity(
        id=kw.get("id", "ent-1"), entity_type=kw.get("entity_type", "movie"),
        title=kw.get("title", "海底小纵队"), content_class="ENTERTAINMENT",
        modality="VIDEO", match_status="none",
    )
    session.add(e)
    session.add(MediaAsset(
        id="asset-1", mount_id="family", path_key="movies/secret-path.mkv",
        size_bytes=1, mtime_ms=1, mime_type="video/mp4", duration_ms=1000,
        playable=True, missing=False, has_poster=False))
    session.commit()
    return e


def test_curator_context_excludes_paths_and_history(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    _entity(session)
    profile = get_profile("library_curator")
    ctx = build_curator_batch_context(session, profile, ["ent-1"])
    assert "海底小纵队" in ctx
    assert "path_key" not in ctx and "secret-path" not in ctx
    assert "viewing" not in ctx and "history" not in ctx


def test_tool_permission_rejects_unlisted_tool():
    profile = get_profile("library_curator")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with pytest.raises(PermissionError):
        ai_tools.call_tool(profile, "get_family_policy", session)  # 儿童侧工具名


# ---------- AC-19：家长/儿童 Tool 注册表双向隔离 ----------

def test_ac19_parent_and_child_tool_registries_disjoint():
    child_src = inspect.getsource(__import__("kindo.agent.tools", fromlist=["tools"]))
    child_names = set(re.findall(r'"name": "([a-z_]+)"', child_src))
    assert len(child_names) == 15  # 儿童侧 15 Tool（技术方案 §8，2026-08-31 增 read_story）
    assert child_names & ai_tools.tool_names() == set()


def test_ac19_parent_tools_are_read_only():
    src = inspect.getsource(ai_tools)
    for banned in ("session.add(", "session.commit(", ".delete(", "session.merge("):
        assert banned not in src, f"家长侧只读工具不得包含写操作: {banned}"


# ---------- S2：Advisor 上下文白名单（§19.6）+ 交叉权限 + schema 校验 ----------

def test_advisor_context_aggregates_without_records_or_paths():
    from datetime import UTC, datetime, timedelta

    from kindo.ai.context import build_advisor_context
    from kindo.models import ContentTopic, InterestSignal

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with session.begin():
        t = ContentTopic(id="t1", name="海洋")
        session.add(t)
        session.flush()
        session.add(ContentEntity(id="ent-a", entity_type="movie", title="海底小纵队",
                                  content_class="ENTERTAINMENT", modality="VIDEO",
                                  match_status="none"))
        session.add(InterestSignal(
            id="s1", profile_id="default", topic_id="t1", signal_type="asked",
            source="ai", created_at=datetime.now(UTC) - timedelta(days=1)))
    profile = get_profile("family_advisor")
    ctx = build_advisor_context(session, profile)
    assert "海洋" in ctx and "asked" in ctx
    # 聚合形态：无逐条观看日志键、无路径
    assert "recent_records" not in ctx
    assert "path_key" not in ctx and "viewing_interval" not in ctx
    assert "library_by_modality" in ctx  # 覆盖统计在场


def test_tool_permission_cross_profile_rejected():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    curator = get_profile("library_curator")
    advisor = get_profile("family_advisor")
    with pytest.raises(PermissionError):
        ai_tools.call_tool(curator, "read_family_stats", session)  # 不在 Curator 清单
    with pytest.raises(PermissionError):
        ai_tools.call_tool(advisor, "read_library_audit_data", session,
                           entity_ids=["x"])  # 不在 Advisor 清单


def test_validate_advisor_summary_schema():
    from kindo.ai.profiles import ADVISOR_SUMMARY_SCHEMA

    good = {"headlines": ["a"], "summary_text": ["b"], "policy_suggestions": [{
        "rules_patch": {"budgets": {"ai_voice_minutes": 5}},
        "summary": {"why": "w", "what": "t", "impact": "i"}}]}
    validate_output(good, ADVISOR_SUMMARY_SCHEMA)
    bad = {"headlines": [], "summary_text": [], "policy_suggestions": [{
        "rules_patch": {}, "summary": {"why": "w", "what": "t", "impact": "i"}}]}
    with pytest.raises(AiRuntimeError):
        validate_output(bad, ADVISOR_SUMMARY_SCHEMA)


def test_validate_advisor_coverage_schema():
    from kindo.ai.profiles import ADVISOR_COVERAGE_SCHEMA

    good = {"headlines": ["a"], "gaps": [{
        "topic": "海洋", "modality": "AUDIO",
        "summary": {"why": "w", "what": "t", "impact": "i"}}]}
    validate_output(good, ADVISOR_COVERAGE_SCHEMA)
    bad = {"headlines": [], "gaps": [{
        "topic": "海洋", "modality": "FILM",
        "summary": {"why": "w", "what": "t", "impact": "i"}}]}
    with pytest.raises(AiRuntimeError):
        validate_output(bad, ADVISOR_COVERAGE_SCHEMA)


def test_ac17_summary_schema_fields_are_observable_only():
    """AC-17 钉住：摘要输出 schema 字段面只含可观察项（防诊断类字段回归）。"""
    from kindo.ai.profiles import ADVISOR_SUMMARY_SCHEMA

    assert set(ADVISOR_SUMMARY_SCHEMA["properties"]) == {
        "headlines", "summary_text", "policy_suggestions"}
    for banned in ("diagnosis", "assessment", "personality", "ability",
                   "psychology", "medical", "rating"):
        assert banned not in ADVISOR_SUMMARY_SCHEMA["properties"]
