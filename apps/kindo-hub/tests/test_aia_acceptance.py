"""家长 AI 助手验收（P16 / AC-16~19 端到端串接；实施计划 §11 S3）。

薄全链路（对齐 test_p2_round 先例）：只串接"任务→建议→家长确认→领域执行"
的完整用户路径与双向权限隔离，不与 test_ai_* 单测重复展开细粒度断言。
LLM 经 FakeRuntime 注入（不触网）。
"""
from __future__ import annotations

import inspect
import json
import re
import time

from kindo.models import ContentEntity

# ---------- 假 Runtime：Curator / Advisor 各按上下文返回固定产出 ----------


class AcceptanceCurator:
    def ready(self) -> bool:
        return True

    def run_ai(self, profile, context_text, output_schema=None):
        entities = json.loads(context_text)["entities"]
        suggestions = []
        for e in entities:
            if not e["overview_present"]:
                suggestions.append({
                    "entity_id": e["entity_id"], "change_type": "set_overview",
                    "changes": {"overview": f"《{e['title']}》的故事简介"},
                    "summary": {"why": "缺少简介", "what": "补充简介",
                                "impact": "家长了解内容更方便"}})
            suggestions.append({  # 对所有实体提议补主题（locked 的会被服务端拦下）
                "entity_id": e["entity_id"], "change_type": "add_topic",
                "changes": {"names": ["海洋"]},
                "summary": {"why": "缺少主题", "what": "补充海洋主题",
                            "impact": "更容易被找到"}})
        suggestions.append({  # HIGH：分类建议（走单决策）
            "entity_id": entities[0]["entity_id"], "change_type": "set_content_class",
            "changes": {"content_class": "LEARNING"},
            "summary": {"why": "疑似分类不当", "what": "调整为学习",
                        "impact": "计入学习视频预算"}})
        return {"findings": [], "suggestions": suggestions}


class AcceptanceAdvisor:
    def ready(self) -> bool:
        return True

    def run_ai(self, profile, context_text, output_schema=None):
        kind = (output_schema or {}).get("x-kind")
        if kind == "advisor_coverage":
            return {"headlines": ["海洋主题相关音频偏少"], "gaps": [{
                "topic": "海洋", "modality": "AUDIO",
                "summary": {"why": "频繁接触海洋主题", "what": "可补充海洋故事音频",
                            "impact": "接力选择更多"}}]}
        return {
            "headlines": ["最近一周海洋主题接触最多", "屏幕时间规则运行正常"],
            "summary_text": ["娱乐视频使用较多，音频较少"],
            "policy_suggestions": [{
                "rules_patch": {"budgets": {"ai_voice_minutes": 5}},
                "summary": {"why": "AI 语音接近用满", "what": "微调 AI 语音时间",
                            "impact": "对话时间更充裕"}}],
        }


def _wait_done(env, job_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = env.client.get(f"/api/v1/admin/ai/jobs/{job_id}").json()
        if body["state"] in ("done", "failed", "interrupted"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"AI 任务未完成: {body}")


def _run_job(env, job_type: str) -> dict:
    r = env.client.post("/api/v1/admin/ai/jobs", json={"job_type": job_type},
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = _wait_done(env, r.json()["job_id"])
    assert body["state"] == "done", body.get("error_summary")
    return body


def _entity(env, eid: str, *, locked_topics=False):
    from kindo.media.metadata import set_field_parent

    with env.db.session() as session:
        e = ContentEntity(id=eid, entity_type="movie", title=f"影片{eid}",
                          content_class="ENTERTAINMENT", modality="VIDEO",
                          match_status="none")
        session.add(e)
        session.flush()
        if locked_topics:
            set_field_parent(session, e, "topics", [], locked=True)
        session.commit()


# ---------- AC-16：媒体库 AI 整理（批量应用 + locked/高影响不被直接覆盖） ----------

def test_ac16_media_library_ai_curation(env):
    env.bootstrap_admin()
    _entity(env, "ac16-a")                    # 缺简介 + 缺主题（LOW×2）
    _entity(env, "ac16-b", locked_topics=True)  # 主题被家长锁定
    env.state._extra["ai_jobs"]._runtime = AcceptanceCurator()

    job = _run_job(env, "CATALOG_AUDIT")
    counts = job["result_summary"]["counts"]
    assert counts["created"] >= 3  # 简介×2 + 未锁定实体的主题

    items = env.client.get("/api/v1/admin/ai/proposals",
                           headers=env.admin_headers()).json()["items"]
    by_entity = {}
    for it in items:
        by_entity.setdefault(it["entity_id"], []).append(it)
    # locked 主题字段：无对应建议（硬性约束 15 / AC-16）
    assert all(it["changes"].get("topics_add") is None
               for it in by_entity["ac16-b"]), "locked 字段不得出现建议"

    # LOW 批量一次应用（不逐项审批）
    low_ids = [it["proposal_id"] for it in items if it["impact_level"] == "LOW"]
    r = env.client.post("/api/v1/admin/ai/proposals/batch-apply",
                        json={"ids": low_ids}, headers=env.admin_headers())
    assert r.status_code == 200
    assert {x["status"] for x in r.json()["results"]} == {"applied"}
    with env.db.session() as session:
        a = session.get(ContentEntity, "ac16-a")
        assert a.overview and (a.meta_provenance_json or {})["overview"]["source"] == "parent"

    # HIGH（content_class）单决策应用：AI 未直接改分类，经家长确认走领域路径
    high = next(it for it in items if it["impact_level"] == "HIGH")
    assert high["entity_id"] == "ac16-a"
    r = env.client.post(f"/api/v1/admin/ai/proposals/{high['proposal_id']}/apply",
                        headers=env.admin_headers())
    assert r.json()["status"] == "applied"
    with env.db.session() as session:
        a = session.get(ContentEntity, "ac16-a")
        assert a.content_class == "LEARNING"
        assert (a.meta_provenance_json or {})["content_class"]["source"] == "parent"


# ---------- AC-17/18：使用摘要仅可观察事实 + 规则建议经确认执行 ----------

def test_ac17_ac18_advisor_chain(env):
    env.bootstrap_admin()
    env.state._extra["ai_jobs"]._runtime = AcceptanceAdvisor()
    v0 = env.client.get("/api/v1/admin/policy").json()["version"]

    job = _run_job(env, "USAGE_SUMMARY")
    # AC-17：result_summary 只含可观察面（headlines/summary_text/counts）
    assert set(job["result_summary"]) <= {"headlines", "summary_text", "counts"}
    assert job["result_summary"]["headlines"]

    # AC-18：规则调整必须经家长确认由 Family Policy Service 执行
    items = env.client.get("/api/v1/admin/ai/proposals",
                           params={"proposal_type": "POLICY"},
                           headers=env.admin_headers()).json()["items"]
    assert len(items) == 1 and items[0]["impact_level"] == "HIGH"
    assert items[0]["policy_diff"], "服务端事实核对的变更前后值必须在场"
    r = env.client.post(f"/api/v1/admin/ai/proposals/{items[0]['proposal_id']}/apply",
                        headers=env.admin_headers())
    body = r.json()
    assert body["status"] == "applied" and body["policy_version"] == v0 + 1
    assert "revoked_playbacks" in body  # Grant 撤销语义与页面保存同路径
    rules = env.client.get("/api/v1/admin/policy").json()["rules"]
    assert rules["budgets"]["ai_voice_minutes"] == 5

    # 方向性建议（CONTENT_GAP）只呈现，无应用动作
    _run_job(env, "CONTENT_COVERAGE")
    gaps = env.client.get("/api/v1/admin/ai/proposals",
                          params={"proposal_type": "CONTENT_GAP"},
                          headers=env.admin_headers()).json()["items"]
    assert len(gaps) == 1
    assert env.client.post(
        f"/api/v1/admin/ai/proposals/{gaps[0]['proposal_id']}/apply",
        headers=env.admin_headers()).status_code == 400


# ---------- AC-19：Agent 权限双向隔离（儿童↔家长） ----------

def test_ac19_agent_permission_isolation():
    from kindo.ai import tools as parent_tools

    child_src = inspect.getsource(__import__("kindo.agent.tools", fromlist=["tools"]))
    child_names = set(re.findall(r'"name": "([a-z_]+)"', child_src))
    assert len(child_names) == 15  # 2026-08-31 增 read_story
    assert child_names & parent_tools.tool_names() == set()

    src = inspect.getsource(parent_tools)
    for banned in ("session.add(", "session.commit(", ".delete(", "session.merge("):
        assert banned not in src, f"家长侧只读工具不得包含写操作: {banned}"

    from kindo.ai.profiles import PROFILES

    assert set(PROFILES) == {"library_curator", "family_advisor"}  # child_companion 不落码
