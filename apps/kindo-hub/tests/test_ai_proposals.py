"""Proposal 生命周期契约测试（技术方案 §19.2~19.4；AC-16 核心）。

覆盖：服务端判级与创建守卫（locked/已有值/去重，AIA-008）、应用执行走既有
领域路径（apply_with_provenance，家长确认=PARENT_EXPLICIT）、basis 快照比对
（事实变化/锁定 → EXPIRED，不基于旧数据执行）、LOW 批量与 HIGH 混入 400。
"""
from __future__ import annotations

import pytest

from kindo.media.metadata import set_field_parent
from kindo.models import (
    AiJob,
    AiProposal,
    ContentEntity,
    ContentTopic,
    EntityTopic,
    Media,
)


def _entity(env, *, eid="ent-1", overview=None, language=None, locked_fields=()):
    with env.db.session() as session:
        e = ContentEntity(
            id=eid, entity_type="movie", title="海底小纵队",
            content_class="ENTERTAINMENT", modality="VIDEO", match_status="none",
            overview=overview, language=language)
        session.add(e)
        session.flush()
        for f in locked_fields:
            set_field_parent(session, e, f, getattr(e, f), locked=True)
        session.commit()
        return eid


def _create(env, entity_id, change_type, changes, parts=None):
    parts = parts or {"why": "资料缺失", "what": "补充资料", "impact": "更容易找到"}
    with env.db.session() as session:
        entity = session.get(ContentEntity, entity_id)
        status = env.state._extra["ai_proposals"].create_from_curator(
            session, job_id="job-t", entity=entity, change_type=change_type,
            changes=changes, summary_parts=parts)
        session.commit()
    return status


def _first_proposal_id(env, entity_id="ent-1"):
    with env.db.session() as session:
        row = (session.query(AiProposal)
               .filter(AiProposal.status == "PENDING")
               .order_by(AiProposal.created_at.desc()).first())
        return row.id if row else None


@pytest.fixture()
def admin(env):
    env.bootstrap_admin()
    return env


# ---------- 创建：服务端判级与守卫 ----------

def test_create_low_and_high_levels(admin):
    _entity(admin, eid="e-low", overview=None)
    _entity(admin, eid="e-high", overview=None)
    assert _create(admin, "e-low", "set_overview", {"overview": "一部海洋动画"}) == "created"
    assert _create(admin, "e-high", "set_content_class",
                   {"content_class": "LEARNING"}) == "created"
    with admin.db.session() as session:
        rows = {r.proposal_type: r for r in session.query(AiProposal).all()}
    assert rows["METADATA"].impact_level in ("LOW", "HIGH")
    high = [r for r in rows.values() if r.impact_level == "HIGH"]
    assert high, "set_content_class 必须判为 HIGH（§19.3）"


def test_create_locked_field_skipped(admin):
    _entity(admin, eid="e-lock", overview=None,
            locked_fields=("overview", "content_class"))
    assert _create(admin, "e-lock", "set_overview", {"overview": "x"}) == "skipped_locked"
    assert _create(admin, "e-lock", "set_content_class",
                   {"content_class": "LEARNING"}) == "skipped_locked"


def test_create_existing_value_and_invalid(admin):
    _entity(admin, eid="e-val", overview="已有简介")
    assert _create(admin, "e-val", "set_overview", {"overview": "新的"}) == "skipped_invalid"
    assert _create(admin, "e-val", "add_topic", {"names": []}) == "skipped_invalid"
    assert _create(admin, "e-val", "add_artwork", {"kind": "poster"}) == "skipped_invalid"  # 无本地资产


def test_create_dedupe_and_reject_no_repeat(admin):
    _entity(admin, eid="e-dup", overview=None)
    assert _create(admin, "e-dup", "set_overview", {"overview": "v1"}) == "created"
    assert _create(admin, "e-dup", "set_overview", {"overview": "v1"}) == "skipped_duplicate"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/reject",
                          headers=admin.admin_headers())
    assert r.status_code == 200
    # 拒绝后同建议不再创建（AIA-008 不重复提醒）
    assert _create(admin, "e-dup", "set_overview", {"overview": "v1"}) == "skipped_duplicate"


# ---------- 应用：走既有领域路径（AC-16） ----------

def test_apply_metadata_writes_parent_provenance(admin):
    _entity(admin, eid="e-apply", overview=None, language=None)
    assert _create(admin, "e-apply", "set_overview", {"overview": "海洋冒险故事"}) == "created"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    with admin.db.session() as session:
        e = session.get(ContentEntity, "e-apply")
        assert e.overview == "海洋冒险故事"
        prov = (e.meta_provenance_json or {})["overview"]
        assert prov["source"] == "parent" and not prov["locked"]  # PARENT_EXPLICIT


def _media_entity(env, *, eid="ent-m", mid="med-1"):
    """带 media 行的叶子实体（movie）——AI 应用需回写 media 展示字段。"""
    with env.db.session() as session:
        session.add(Media(id=mid, mount_id="mnt-1", path_key=f"/{mid}.mp4",
                          title="测试影片", media_type="movie"))
        session.add(ContentEntity(
            id=eid, entity_type="movie", title="测试影片",
            content_class="ENTERTAINMENT", modality="VIDEO", match_status="none",
            source_media_id=mid))
        session.commit()
    return eid


def test_apply_metadata_syncs_media_row(admin):
    """2026-08-28 修复：应用建议只写 entity 而 Admin 媒体库/TV 浏览读 media 行
    ——language/age_band 必须回写（含 parent_edited_json + metadata_version+1，
    与手动 PATCH 同语义，重扫不覆盖）。"""
    _media_entity(admin, eid="e-sync", mid="med-sync")
    assert _create(admin, "e-sync", "set_language",
                   {"language": "en"}) == "created"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    with admin.db.session() as session:
        m = session.get(Media, "med-sync")
        assert m.language == "en"
        assert (m.parent_edited_json or {}).get("language") == "en"
        assert m.metadata_version == 2  # 1 → +1
        e = session.get(ContentEntity, "e-sync")
        assert e.language == "en"  # entity 同步为 canonical 值


def test_apply_age_range_syncs_media_age_band(admin):
    _media_entity(admin, eid="e-age", mid="med-age")
    assert _create(admin, "e-age", "set_age_range",
                   {"age_min": 0, "age_max": 5}) == "created"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    with admin.db.session() as session:
        m = session.get(Media, "med-age")
        assert m.age_band == "0-5"  # 与 parse_age_band 可逆的文本格式
        assert (m.parent_edited_json or {}).get("age_band") == "0-5"


def test_apply_metadata_entity_without_media_row_ok(admin):
    """series/season 等无 source_media_id 的实体：无行可回写，应用照常成功。"""
    _entity(admin, eid="e-nomedia", overview=None)  # _entity 不带 source_media_id
    assert _create(admin, "e-nomedia", "set_age_range",
                   {"age_min": 2, "age_max": 6}) == "created"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"


def test_create_pending_same_target_deduped(admin):
    """AIA-008 收紧：同实体同目标面已有 PENDING 时，不同值也不再新增（否则
    每轮全库审计都会以不同值绕过 dedupe_key 堆积建议）；不同目标面不受影响。"""
    _entity(admin, eid="e-flood", overview=None, language=None)
    p1 = {"why": "缺简介一", "what": "补一", "impact": "更易找"}
    assert _create(admin, "e-flood", "set_overview",
                   {"overview": "v1"}, parts=p1) == "created"
    assert _create(admin, "e-flood", "set_overview", {"overview": "v2"}) == "skipped_duplicate"
    assert _create(admin, "e-flood", "set_language",
                   {"language": "en"}) == "created"  # 不同字段可并存
    # 处理完（拒绝）后同字段可再提新建议
    with admin.db.session() as session:
        pid = (session.query(AiProposal)
               .filter(AiProposal.status == "PENDING",
                       AiProposal.summary.like("%缺简介一%")).first().id)
        session.commit()
    assert admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/reject",
                             headers=admin.admin_headers()).status_code == 200
    assert _create(admin, "e-flood", "set_overview", {"overview": "v3"}) == "created"


def test_apply_add_topic_links_and_syncs(admin):
    _entity(admin, eid="e-topic")
    assert _create(admin, "e-topic", "add_topic", {"names": ["海洋", "合作"]}) == "created"
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200
    with admin.db.session() as session:
        names = [t[0] for t in (
            session.query(ContentTopic.name)
            .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
            .filter(EntityTopic.entity_id == "e-topic").all())]
        assert set(names) == {"海洋", "合作"}
        e = session.get(ContentEntity, "e-topic")
        assert (e.meta_provenance_json or {})["topics"]["source"] == "parent"


# ---------- 过期：basis 快照直接比对（§19.4 重读事实） ----------

def test_apply_expired_when_provenance_changed(admin):
    _entity(admin, eid="e-stale", overview=None)
    assert _create(admin, "e-stale", "set_overview", {"overview": "旧建议"}) == "created"
    # 生成后家长手动编辑了同一字段（provenance 变化）
    with admin.db.session() as session:
        set_field_parent(session, session.get(ContentEntity, "e-stale"),
                         "overview", "家长已填", locked=False)
        session.commit()
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "expired"
    with admin.db.session() as session:
        e = session.get(ContentEntity, "e-stale")
        assert e.overview == "家长已填"  # 旧建议未执行
        row = session.get(AiProposal, pid)
        assert row.status == "EXPIRED"


def test_apply_expired_when_locked_after_creation(admin):
    _entity(admin, eid="e-lock2", overview=None)
    assert _create(admin, "e-lock2", "set_overview", {"overview": "v"}) == "created"
    with admin.db.session() as session:
        set_field_parent(session, session.get(ContentEntity, "e-lock2"),
                         "overview", "家长值", locked=True)
        session.commit()
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.json()["status"] == "expired"  # locked 字段永不被覆盖（硬性约束 15 / AC-16）


def test_apply_expired_when_entity_deleted(admin):
    _entity(admin, eid="e-gone", overview=None)
    assert _create(admin, "e-gone", "set_overview", {"overview": "v"}) == "created"
    with admin.db.session() as session:
        session.delete(session.get(ContentEntity, "e-gone"))
        session.commit()
    pid = _first_proposal_id(admin)
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.json()["status"] == "expired"


# ---------- 批量：LOW 一次应用，HIGH 混入 400（AIA-002） ----------

def _make_low_high(admin):
    _entity(admin, eid="b-1", overview=None)
    _entity(admin, eid="b-2", overview=None)
    _entity(admin, eid="b-high", overview=None)
    assert _create(admin, "b-1", "set_overview", {"overview": "一"}) == "created"
    assert _create(admin, "b-2", "set_language", {"language": "zh-CN"}) == "created"
    assert _create(admin, "b-high", "set_content_class",
                   {"content_class": "LEARNING"}) == "created"
    with admin.db.session() as session:
        rows = (session.query(AiProposal)
                .filter(AiProposal.status == "PENDING").all())
        low_ids = [r.id for r in rows if r.impact_level == "LOW"]
        high_id = [r.id for r in rows if r.impact_level == "HIGH"][0]
    return low_ids, high_id


def test_batch_apply_rejects_high(admin):
    low_ids, high_id = _make_low_high(admin)
    r = admin.client.post("/api/v1/admin/ai/proposals/batch-apply",
                          json={"ids": low_ids + [high_id]},
                          headers=admin.admin_headers())
    assert r.status_code == 400  # 整体不执行
    with admin.db.session() as session:
        assert (session.query(AiProposal)
                .filter(AiProposal.status == "APPLIED").count()) == 0


def test_batch_apply_lows(admin):
    low_ids, _high = _make_low_high(admin)
    r = admin.client.post("/api/v1/admin/ai/proposals/batch-apply",
                          json={"ids": low_ids}, headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    statuses = {x["proposal_id"]: x["status"] for x in r.json()["results"]}
    assert set(statuses.values()) == {"applied"}


def test_dismiss_all_clears_pending_and_regenerable(admin):
    """全部忽略、清掉重来（2026-08-28 用户决策）：PENDING 行全删（下次审计可
    重新生成——区别于单条忽略 REJECTED 的同建议不再提醒），APPLIED 保留；
    列表带 total；CATALOG_AUDIT 运行中 409。"""
    _entity(admin, eid="e-clear", overview=None, language=None)
    assert _create(admin, "e-clear", "set_language", {"language": "en"}) == "created"
    assert _create(admin, "e-clear", "set_overview", {"overview": "v"}) == "created"
    pid = _first_proposal_id(admin)  # 最新 = set_overview
    assert (admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                              headers=admin.admin_headers()).json()["status"] == "applied")
    r = admin.client.get("/api/v1/admin/ai/proposals", headers=admin.admin_headers())
    assert r.json()["total"] == 1  # 仅剩 set_language PENDING
    # 运行中任务 → 409 整体不清
    with admin.db.session() as session:
        session.add(AiJob(id="job-run", job_type="CATALOG_AUDIT", state="running"))
        session.commit()
    r = admin.client.post("/api/v1/admin/ai/proposals/dismiss-all",
                          headers=admin.admin_headers())
    assert r.status_code == 409
    with admin.db.session() as session:
        session.get(AiJob, "job-run").state = "done"
        session.commit()
    # 正常清空：PENDING 全删、APPLIED 保留
    r = admin.client.post("/api/v1/admin/ai/proposals/dismiss-all",
                          headers=admin.admin_headers())
    assert r.status_code == 200 and r.json()["cleared"] == 1
    with admin.db.session() as session:
        assert {x.status for x in session.query(AiProposal).all()} == {"APPLIED"}
    # 删除后同建议可重新生成（REJECTED 才会被 dedupe 挡住）
    assert _create(admin, "e-clear", "set_language", {"language": "en"}) == "created"


def test_apply_state_transitions_guard(admin):
    _entity(admin, eid="e-twice", overview=None)
    assert _create(admin, "e-twice", "set_overview", {"overview": "v"}) == "created"
    pid = _first_proposal_id(admin)
    assert admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                             headers=admin.admin_headers()).json()["status"] == "applied"
    # 已应用后再次应用 → 400
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 400


def test_content_gap_not_applicable(admin):
    with admin.db.session() as session:
        session.add(AiProposal(
            id="gap-1", profile="family_advisor", proposal_type="CONTENT_GAP",
            summary="s", payload_json={"action": "info"}, impact_level="LOW",
            status="PENDING", dedupe_key="k-gap"))
        session.commit()
    r = admin.client.post("/api/v1/admin/ai/proposals/gap-1/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 400  # 方向性建议无应用操作（AIA-005）


def test_apply_high_content_class_single_decision(admin):
    """M-3（S1 评审）：HIGH 分类建议经单决策端点应用成功，走 PARENT_EXPLICIT
    且 provenance 正确（AC-16 高影响分支显式断言）；应用后重复应用 → 400。"""
    _entity(admin, eid="e-high-apply", overview=None)
    assert _create(admin, "e-high-apply", "set_content_class",
                   {"content_class": "LEARNING"}) == "created"
    with admin.db.session() as session:
        row = (session.query(AiProposal)
               .filter(AiProposal.impact_level == "HIGH",
                       AiProposal.status == "PENDING")
               .order_by(AiProposal.created_at.desc()).first())
        assert row is not None and row.impact_level == "HIGH"
        pid = row.id
    r = admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    with admin.db.session() as session:
        e = session.get(ContentEntity, "e-high-apply")
        assert e.content_class == "LEARNING"
        prov = (e.meta_provenance_json or {})["content_class"]
        assert prov["source"] == "parent" and not prov["locked"]
        assert session.get(AiProposal, pid).status == "APPLIED"
    # 已处理后再次应用 → 400（单决策一次性）
    assert admin.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                             headers=admin.admin_headers()).status_code == 400


# ---------- S2：POLICY 建议（AIA-004 / AC-18）与 CONTENT_GAP ----------

def _advisor(env, kind, payload_parts, parts=None):
    parts = parts or {"why": "娱乐长期用满", "what": "微调预算", "impact": "更均衡"}
    with env.db.session() as session:
        status = env.state._extra["ai_proposals"].create_from_advisor(
            session, job_id="job-s2", kind=kind,
            payload_parts=payload_parts, summary_parts=parts)
        session.commit()
    return status


def _policy_version(env) -> int:
    r = env.client.get("/api/v1/admin/policy")
    assert r.status_code == 200
    return r.json()["version"]


def test_policy_proposal_created_high_and_applied(env):
    env.bootstrap_admin()
    v0 = _policy_version(env)
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"budgets": {"ai_voice_minutes": 5}}}) == "created"
    with env.db.session() as session:
        row = (session.query(AiProposal)
               .filter(AiProposal.proposal_type == "POLICY").one())
        assert row.impact_level == "HIGH"  # 一切 Policy 修改=HIGH（§6.2）
        pid = row.id
    r = env.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "applied"
    assert body["policy_version"] == v0 + 1  # version+1（AC-18）
    assert "revoked_playbacks" in body       # Grant 撤销语义与页面保存一致
    rules = env.client.get("/api/v1/admin/policy").json()["rules"]
    assert rules["budgets"]["ai_voice_minutes"] == 5  # deep merge 生效


def test_policy_proposal_expired_when_version_changed(env):
    env.bootstrap_admin()
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"budgets": {"ai_voice_minutes": 7}}}) == "created"
    # 生成后家长在页面改了规则（version 前进）
    rules = env.client.get("/api/v1/admin/policy").json()["rules"]
    rules["budgets"]["ai_voice_minutes"] = 9
    r = env.client.put("/api/v1/admin/policy", json=rules,
                       headers=env.admin_headers())
    assert r.status_code == 200
    with env.db.session() as session:
        pid = (session.query(AiProposal)
               .filter(AiProposal.proposal_type == "POLICY",
                       AiProposal.status == "PENDING").one()).id
    r = env.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                        headers=env.admin_headers())
    body = r.json()
    assert body["status"] == "expired"  # 不基于旧版本执行（§19.4）
    assert env.client.get("/api/v1/admin/policy").json()["rules"]["budgets"]["ai_voice_minutes"] == 9


def test_policy_patch_guards(env):
    env.bootstrap_admin()
    # 不允许的顶层键 / 垃圾值 / 空补丁 → 不落库
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"content_scope": {"banned_tags": ["x"]}}}) == "skipped_invalid"
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"budgets": {"ai_voice_minutes": -3}}}) == "skipped_invalid"
    assert _advisor(env, "POLICY", {"rules_patch": {}}) == "skipped_invalid"
    # 同补丁去重
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"budgets": {"ai_voice_minutes": 5}}}) == "created"
    assert _advisor(env, "POLICY",
                    {"rules_patch": {"budgets": {"ai_voice_minutes": 5}}}) == "skipped_duplicate"


def test_content_gap_created_low_not_applicable_nor_batchable(env):
    env.bootstrap_admin()
    assert _advisor(env, "CONTENT_GAP",
                    {"topic": "海洋", "modality": "AUDIO"}) == "created"
    with env.db.session() as session:
        row = (session.query(AiProposal)
               .filter(AiProposal.proposal_type == "CONTENT_GAP").one())
        assert row.impact_level == "LOW"
        pid = row.id
    r = env.client.post(f"/api/v1/admin/ai/proposals/{pid}/apply",
                        headers=env.admin_headers())
    assert r.status_code == 400  # 方向性建议无应用操作（AIA-005）
    r = env.client.post("/api/v1/admin/ai/proposals/batch-apply", json={"ids": [pid]},
                        headers=env.admin_headers())
    assert r.status_code == 400


def test_policy_proposal_view_shows_server_factored_diff(env):
    """S2 评审 M-1：建议视图返回服务端事实核对的变更前后值（不信任 LLM 文案）。"""
    env.bootstrap_admin()
    rules = env.client.get("/api/v1/admin/policy").json()["rules"]
    rules.setdefault("budgets", {})["video_by_class"] = {"ENTERTAINMENT": 40}
    r = env.client.put("/api/v1/admin/policy", json=rules, headers=env.admin_headers())
    assert r.status_code == 200
    assert _advisor(env, "POLICY", {"rules_patch": {
        "budgets": {"video_by_class": {"ENTERTAINMENT": 35}}}}) == "created"
    r = env.client.get("/api/v1/admin/ai/proposals",
                       params={"proposal_type": "POLICY"},
                       headers=env.admin_headers())
    item = r.json()["items"][0]
    assert item["policy_diff"] == ["动画（娱乐）时间：40 → 35"]


def test_batch_apply_high_with_list_confirmation(admin):
    """清单式一次确认（产品决策 2026-08-27）：allow_high=True 时高影响建议可
    批量应用（界面完整呈现前后值后一次确认）；无旗标混入 HIGH 仍 400。"""
    low_ids, high_id = _make_low_high(admin)
    # 无旗标：整体 400（既有语义不变）
    r = admin.client.post("/api/v1/admin/ai/proposals/batch-apply",
                          json={"ids": low_ids + [high_id]},
                          headers=admin.admin_headers())
    assert r.status_code == 400
    # 清单式确认：LOW + HIGH 一次应用
    r = admin.client.post("/api/v1/admin/ai/proposals/batch-apply",
                          json={"ids": low_ids + [high_id], "allow_high": True},
                          headers=admin.admin_headers())
    assert r.status_code == 200, r.text
    statuses = {x["proposal_id"]: x["status"] for x in r.json()["results"]}
    assert set(statuses.values()) == {"applied"}
    with admin.db.session() as session:
        e = session.get(ContentEntity, "b-high")
        assert e.content_class == "LEARNING"  # 高影响项经同一领域路径生效
