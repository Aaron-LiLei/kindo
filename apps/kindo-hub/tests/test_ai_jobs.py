"""AI Job Runner 契约测试（技术方案 §19.5；对齐 scan_job 模式）。

LLM 经 FakeRuntime 注入（不触网）：三端点流转、同类单飞 409、真实进度、
result_summary 落库；LLM 未配置 → 503；重启 running → interrupted。
"""
from __future__ import annotations

import json
import time

from kindo.ai.runtime import AiRuntimeError
from kindo.models import AiJob, ContentEntity


class FakeRuntime:
    """按批返回固定建议：每个实体一个 add_topic + 一条 finding。
    run_ai 形参与 LLMRuntime 一致（含 output_schema 覆盖参数）。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def ready(self) -> bool:
        return True

    def run_ai(self, profile, context_text: str, output_schema: dict | None = None) -> dict:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        entities = json.loads(context_text).get("entities", [])
        return {
            "findings": [{"entity_id": entities[0]["entity_id"], "issue": "疑似重复归组"}]
            if entities else [],
            "suggestions": [
                {
                    "entity_id": e["entity_id"],
                    "change_type": "add_topic",
                    "changes": {"names": ["海洋"]},
                    "summary": {"why": "缺少主题", "what": "补充海洋主题",
                                "impact": "更容易被找到"},
                }
                for e in entities
            ],
        }


def _add_entity(env, eid: str):
    with env.db.session() as session:
        session.add(ContentEntity(
            id=eid, entity_type="movie", title=f"影片{eid}",
            content_class="ENTERTAINMENT", modality="VIDEO", match_status="none"))
        session.commit()


def _wait_done(env, job_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = env.client.get(f"/api/v1/admin/ai/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["state"] in ("done", "failed", "interrupted"):
            return body
        time.sleep(0.1)
    raise AssertionError("AI 任务未在超时内完成")


def test_ai_job_requires_provider(env):
    """未配置 LLM Provider → 503（provider_unavailable），不建任务。"""
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CATALOG_AUDIT"},
                        headers=env.admin_headers())
    assert r.status_code == 503
    with env.db.session() as session:
        assert session.query(AiJob).count() == 0


def test_catalog_audit_end_to_end(env):
    env.bootstrap_admin()
    _add_entity(env, "j-1")
    _add_entity(env, "j-2")
    fake = FakeRuntime()
    env.state._extra["ai_jobs"]._runtime = fake

    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CATALOG_AUDIT"},
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    body = _wait_done(env, job_id)
    assert body["state"] == "done"
    assert body["progress"] == 1.0
    counts = body["result_summary"]["counts"]
    assert counts["audited"] == 2 and counts["created"] == 2
    assert any("疑似重复归组" in h for h in body["result_summary"]["headlines"])

    # 建议列表带实体标题（家长可读）
    r = env.client.get("/api/v1/admin/ai/proposals", headers=env.admin_headers())
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["entity_title"] for i in items} == {"影片j-1", "影片j-2"}
    assert all(i["impact_level"] == "LOW" for i in items)


def test_single_flight_409(env):
    env.bootstrap_admin()
    _add_entity(env, "s-1")
    env.state._extra["ai_jobs"]._runtime = FakeRuntime(delay=0.8)
    r1 = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CATALOG_AUDIT"},
                         headers=env.admin_headers())
    assert r1.status_code == 200
    r2 = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CATALOG_AUDIT"},
                         headers=env.admin_headers())
    assert r2.status_code == 409
    _wait_done(env, r1.json()["job_id"])
    # 终态后可再次发起
    r3 = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CATALOG_AUDIT"},
                         headers=env.admin_headers())
    assert r3.status_code == 200


def test_unknown_job_type_rejected_pre_s2(env):
    """S1 时代的"未实现类型 400"断言已随 S2 失效：三类任务全部可用，
    未知类型仍 400（S2 版完整断言见文件尾 test_unknown_job_type_only_rejected）。"""
    env.bootstrap_admin()
    env.state._extra["ai_jobs"]._runtime = FakeRuntime()
    for jt in ("CATALOG_AUDIT", "USAGE_SUMMARY", "CONTENT_COVERAGE"):
        r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": jt},
                            headers=env.admin_headers())
        assert r.status_code == 200, jt
        body = _wait_done(env, r.json()["job_id"])
        assert body["state"] == "done", body.get("error_summary")
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "NOPE"},
                        headers=env.admin_headers())
    assert r.status_code == 400


def test_job_failed_on_runtime_error(env):
    env.bootstrap_admin()
    _add_entity(env, "f-1")

    class Boom(FakeRuntime):
        def run_ai(self, profile, ctx):
            raise AiRuntimeError("模型输出无法解析")

    env.state._extra["ai_jobs"]._runtime = Boom()
    job_id = env.client.post("/api/v1/admin/ai/jobs",
                             json={"job_type": "CATALOG_AUDIT"},
                             headers=env.admin_headers()).json()["job_id"]
    body = _wait_done(env, job_id)
    assert body["state"] == "failed"
    assert "模型输出" in body["error_summary"]


def test_job_failed_error_summary_includes_exception_type(env):
    """2026-08-28 修复：str(exc) 为空的异常（如裸 CancelledError）曾把
    error_summary 存成空串——家长只看到"失败"没有原因。"""
    env.bootstrap_admin()
    _add_entity(env, "f-2")

    class SilentBoom(FakeRuntime):
        def run_ai(self, profile, ctx):
            raise ValueError()  # str() == ""

    env.state._extra["ai_jobs"]._runtime = SilentBoom()
    job_id = env.client.post("/api/v1/admin/ai/jobs",
                             json={"job_type": "CATALOG_AUDIT"},
                             headers=env.admin_headers()).json()["job_id"]
    body = _wait_done(env, job_id)
    assert body["state"] == "failed"
    assert body["error_summary"].startswith("ValueError")


def test_restart_marks_interrupted(env):
    env.bootstrap_admin()
    with env.db.session() as session:
        session.add(AiJob(id="job-x", job_type="CATALOG_AUDIT", state="running"))
        session.commit()
    env.state._extra["ai_jobs"].mark_interrupted_on_startup()
    with env.db.session() as session:
        assert session.get(AiJob, "job-x").state == "interrupted"


# ---------- S2：Advisor 两任务端到端 ----------

class SummaryFakeRuntime(FakeRuntime):
    def run_ai(self, profile, context_text, output_schema=None):
        self.calls += 1
        assert profile.profile_id == "family_advisor"
        assert "recent_records" not in context_text  # 聚合上下文（§19.6）
        return {
            "headlines": ["最近一周海洋主题接触最多"],
            "summary_text": ["娱乐视频用得较多，音频较少", "成长接力接受率良好"],
            "policy_suggestions": [{
                "rules_patch": {"budgets": {"ai_voice_minutes": 5}},
                "summary": {"why": "AI 语音接近用满", "what": "微调 AI 语音时间",
                            "impact": "对话时间更充裕"}}],
        }


class CoverageFakeRuntime(FakeRuntime):
    def run_ai(self, profile, context_text, output_schema=None):
        self.calls += 1
        assert output_schema is not None and output_schema.get("x-kind") == "advisor_coverage"
        return {
            "headlines": ["海洋主题相关音频偏少"],
            "gaps": [{
                "topic": "海洋", "modality": "AUDIO",
                "summary": {"why": "孩子频繁接触海洋主题", "what": "可补充海洋故事音频",
                            "impact": "动画结束后有更多接力选择"}}],
        }


def test_usage_summary_end_to_end(env):
    env.bootstrap_admin()
    env.state._extra["ai_jobs"]._runtime = SummaryFakeRuntime()
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "USAGE_SUMMARY"},
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = _wait_done(env, r.json()["job_id"])
    assert body["state"] == "done"
    assert body["result_summary"]["headlines"] == ["最近一周海洋主题接触最多"]
    assert len(body["result_summary"]["summary_text"]) == 2
    assert body["result_summary"]["counts"]["policy_created"] == 1
    r = env.client.get("/api/v1/admin/ai/proposals",
                       params={"proposal_type": "POLICY"},
                       headers=env.admin_headers())
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["impact_level"] == "HIGH"
    assert items[0]["summary_parts"]["why"] == "AI 语音接近用满"


def test_content_coverage_end_to_end(env):
    env.bootstrap_admin()
    env.state._extra["ai_jobs"]._runtime = CoverageFakeRuntime()
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "CONTENT_COVERAGE"},
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = _wait_done(env, r.json()["job_id"])
    assert body["state"] == "done"
    assert body["result_summary"]["counts"]["gap_created"] == 1
    r = env.client.get("/api/v1/admin/ai/proposals",
                       params={"proposal_type": "CONTENT_GAP"},
                       headers=env.admin_headers())
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["changes"]["topic"] == "海洋"


def test_unknown_job_type_only_rejected(env):
    """S2 后三类任务全部可用；仅未知类型 400。"""
    env.bootstrap_admin()
    env.state._extra["ai_jobs"]._runtime = SummaryFakeRuntime()
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": "NOPE"},
                        headers=env.admin_headers())
    assert r.status_code == 400


def test_catalog_audit_live_process_visible(env):
    """产品反馈（2026-08-27）：不能只给百分比——运行中 job 的 result_summary
    携带过程快照（stage_note / processed / total / 累计计数）。"""
    env.bootstrap_admin()
    _add_entity(env, "lv-1")
    _add_entity(env, "lv-2")
    env.state._extra["ai_jobs"]._runtime = FakeRuntime(delay=1.2)
    job_id = env.client.post("/api/v1/admin/ai/jobs",
                             json={"job_type": "CATALOG_AUDIT"},
                             headers=env.admin_headers()).json()["job_id"]
    seen = None
    for _ in range(40):
        body = env.client.get(f"/api/v1/admin/ai/jobs/{job_id}").json()
        if body["state"] == "running" and body.get("result_summary"):
            seen = body
            break
        time.sleep(0.05)
    assert seen is not None, "未捕捉到运行中过程快照"
    rs = seen["result_summary"]
    assert rs.get("stage_note"), "阶段说明必须在场"
    assert "processed" in rs and rs.get("total") == 2
    _wait_done(env, job_id)
    final = env.client.get(f"/api/v1/admin/ai/jobs/{job_id}").json()["result_summary"]
    assert final["counts"]["audited"] == 2 and final["counts"]["created"] == 2
