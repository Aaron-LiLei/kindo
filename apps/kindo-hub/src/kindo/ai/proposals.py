"""Proposal 领域逻辑（技术方案 §19.2~19.4；AIA-002/007/008）。

创建：服务端校验（不信任 LLM 分类）→ 判级（LOW/HIGH 服务器定）→ 生成 basis
事实快照 → 去重（同键 PENDING/APPLIED/REJECTED 不重复创建，AIA-008）。
应用（§19.4）：payload.basis 明文与库内现状逐字段直接比对（不重建哈希），
任一不符 → EXPIRED；通过后经既有领域路径执行（A-20：Agent 不直接写库）：
- METADATA → media.metadata.apply_with_provenance（家长确认=PARENT_EXPLICIT，
  locked 越级写入被拒）+ 主题/角色追加（与 Admin PATCH 同步语义）
- ARTWORK  → media.artwork 本地帧生成（仅实体有本地主视频资产时创建建议）
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..errors import invalid_request, not_found
from ..media.metadata import apply_with_provenance
from ..models import (
    AiProposal,
    ArtworkAsset,
    ContentCharacter,
    ContentEntity,
    ContentTopic,
    EntityAsset,
    EntityCharacter,
    EntityTopic,
    Media,
    MediaAsset,
)
from ..util import new_id, now_iso

logger = logging.getLogger("kindo.ai.proposals")

CONTENT_CLASSES = ("ENTERTAINMENT", "LEARNING", "STORY", "MUSIC", "OTHER")
ARTWORK_KINDS = ("poster", "backdrop", "thumbnail", "logo")
MAX_NAMES_PER_SUGGESTION = 5

# POLICY rules_patch 允许触碰的顶层键（v2 可编辑面；v1 兼容键与 content_scope
# 不开放给 AI 补丁——分类/屏蔽属内容事实与高敏面，走既有页面）
POLICY_PATCH_KEYS = (
    "budgets", "offscreen", "transition_policy",
    "allowed_windows", "autoplay", "course_counts_as_entertainment",
    "daily_episode_limit",
)

# change_type → (proposal_type, impact_level)（§19.3：判级在服务端，不信任模型）
CHANGE_TYPE_MAP = {
    "add_topic": ("METADATA", "LOW"),
    "add_character": ("METADATA", "LOW"),
    "set_overview": ("METADATA", "LOW"),
    "set_language": ("METADATA", "LOW"),
    "add_artwork": ("ARTWORK", "LOW"),
    "set_content_class": ("METADATA", "HIGH"),
    "set_age_range": ("METADATA", "HIGH"),
}

STATUS_ACTIVE_FOR_DEDUPE = ("PENDING", "APPLIED", "REJECTED")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _deep_merge(base: dict, patch: dict) -> dict:
    """dict 递归合并（标量/列表整体替换）——POLICY rules_patch 的合并语义。"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# POLICY 建议差异行的家长可读标签（交互 §8.2.1：高影响示例需展示"40 → 35 分钟"）
_POLICY_DIFF_LABELS = {
    ("budgets", "screen_total_minutes"): "总屏幕时间",
    ("budgets", "audio_minutes"): "音频时间",
    ("budgets", "ai_voice_minutes"): "AI 语音时间",
    ("offscreen", "allowed"): "离屏活动",
    ("offscreen", "offer_enabled"): "离屏活动推荐",
    ("transition_policy", "enabled"): "成长接力",
    ("transition_policy", "max_minutes"): "成长接力时间盒",
    ("transition_policy", "daily_offer_limit"): "成长接力每日次数",
    ("autoplay",): "自动连播",
    ("daily_episode_limit",): "每日集数上限",
    ("course_counts_as_entertainment",): "课程计入方式",
    ("allowed_windows",): "可观看时间段",
}
_CLASS_DIFF_LABELS = {"ENTERTAINMENT": "动画（娱乐）时间", "LEARNING": "学习视频时间"}


def _patch_leaves(patch: dict, path: tuple = ()):
    for k, v in patch.items():
        if isinstance(v, dict):
            yield from _patch_leaves(v, path + (k,))
        else:
            yield path + (k,), v


def _get_path(data: dict, path: tuple):
    cur: object = data
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _fmt_policy_value(v) -> str:
    if v is None:
        return "未设置"
    if isinstance(v, bool):
        return "开" if v else "关"
    if isinstance(v, list):
        return "已调整"
    return str(v)


def policy_diff_lines(current_rules_json: dict, patch: dict) -> list[str]:
    """服务端事实核对的变更前后值（不信任 LLM 自述文案；M-1 评审修复）。"""
    merged = _deep_merge(current_rules_json, patch)
    lines: list[str] = []
    for path, _new in _patch_leaves(patch):
        before = _get_path(current_rules_json, path)
        after = _get_path(merged, path)
        if before == after:
            continue
        if len(path) == 3 and path[0] == "budgets" and path[1] == "video_by_class":
            label = _CLASS_DIFF_LABELS.get(path[2], "分类视频时间")
        else:
            label = _POLICY_DIFF_LABELS.get(path, "规则项")
        lines.append(f"{label}：{_fmt_policy_value(before)} → {_fmt_policy_value(after)}")
    return lines[:8] or ["规则细节有调整"]


def _prov_sig(entity: ContentEntity, field: str) -> dict:
    raw = (entity.meta_provenance_json or {}).get(field) or {}
    return {"source": raw.get("source") or "", "locked": bool(raw.get("locked"))}


def _clean_names(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [n.strip() for n in value if isinstance(n, str) and n.strip()]


def _summary_text(parts: dict) -> str:
    return (f"为什么：{parts.get('why', '')}；"
            f"将修改：{parts.get('what', '')}；"
            f"影响：{parts.get('impact', '')}")


def _local_primary_asset(session: Session, entity_id: str, storage) -> tuple[str, str] | None:
    """返回 (asset_id, mount_id)：实体首个本地源可用主视频资产。"""
    rows = (
        session.query(EntityAsset.asset_id, MediaAsset.mount_id)
        .join(MediaAsset, MediaAsset.id == EntityAsset.asset_id)
        .filter(EntityAsset.entity_id == entity_id,
                EntityAsset.role == "PRIMARY_VIDEO",
                MediaAsset.playable.is_(True),
                MediaAsset.missing.is_(False))
        .all())
    for asset_id, mount_id in rows:
        if storage is None:
            return asset_id, mount_id
        try:
            provider = storage.get(mount_id)
        except Exception:
            continue
        if provider is not None and hasattr(provider, "abs_path"):
            return asset_id, mount_id
    return None


class ProposalService:
    def __init__(self, db_session_factory, config, storage=None,
                 policy_engine=None, playback=None):
        self._db = db_session_factory
        self._config = config
        self._storage = storage
        self._policy = policy_engine
        self._playback = playback

    # ---------- 创建（job worker 持有 session；只 add，不 commit） ----------

    def create_from_curator(self, session: Session, *, job_id: str | None,
                            entity: ContentEntity, change_type: str,
                            changes: dict, summary_parts: dict) -> str:
        """服务端校验 + 判级 + basis + 去重；返回 created / skipped_*。"""
        mapped = CHANGE_TYPE_MAP.get(change_type or "")
        if mapped is None:
            return "skipped_invalid"
        proposal_type, impact_level = mapped
        changes = changes if isinstance(changes, dict) else {}
        parts = {
            "why": str(summary_parts.get("why") or "").strip(),
            "what": str(summary_parts.get("what") or "").strip(),
            "impact": str(summary_parts.get("impact") or "").strip(),
        }
        if not all(parts.values()):
            return "skipped_invalid"

        payload: dict = {"action": "apply_fields", "entity_id": entity.id}
        basis_fields: dict[str, dict] = {}

        if change_type == "add_topic" or change_type == "add_character":
            field = "topics" if change_type == "add_topic" else "characters"
            if _prov_sig(entity, field)["locked"]:
                return "skipped_locked"
            existing = set(self._linked_names(session, entity.id, topic=(field == "topics")))
            names = [n for n in _clean_names(changes.get("names"))[:MAX_NAMES_PER_SUGGESTION]
                     if n not in existing]
            if not names:
                return "skipped_invalid"
            payload["topics_add" if field == "topics" else "characters_add"] = sorted(names)
            basis_fields[field] = _prov_sig(entity, field)
        elif change_type in ("set_overview", "set_language", "set_content_class"):
            field = {"set_overview": "overview", "set_language": "language",
                     "set_content_class": "content_class"}[change_type]
            if _prov_sig(entity, field)["locked"]:
                return "skipped_locked"
            value = changes.get(field)
            if not isinstance(value, str) or not value.strip():
                return "skipped_invalid"
            if change_type == "set_content_class":
                if value not in CONTENT_CLASSES or value == getattr(entity, field):
                    return "skipped_invalid"
            elif getattr(entity, field):  # 已有值不覆盖（只补缺）
                return "skipped_invalid"
            payload["fields"] = {field: value.strip()}
            basis_fields[field] = _prov_sig(entity, field)
        elif change_type == "set_age_range":
            locked = _prov_sig(entity, "age_min")["locked"] or _prov_sig(entity, "age_max")["locked"]
            if locked:
                return "skipped_locked"
            age_min, age_max = changes.get("age_min"), changes.get("age_max")
            for v in (age_min, age_max):
                if v is not None and (not isinstance(v, int) or isinstance(v, bool)
                                      or not 0 <= v <= 18):
                    return "skipped_invalid"
            if age_min is not None and age_max is not None and age_min > age_max:
                return "skipped_invalid"
            if (age_min, age_max) == (entity.age_min, entity.age_max):
                return "skipped_invalid"
            payload["fields"] = {"age_min": age_min, "age_max": age_max}
            basis_fields["age_min"] = _prov_sig(entity, "age_min")
            basis_fields["age_max"] = _prov_sig(entity, "age_max")
        elif change_type == "add_artwork":
            kind = changes.get("kind")
            if kind not in ARTWORK_KINDS:
                return "skipped_invalid"
            exists = (session.query(ArtworkAsset)
                      .filter(ArtworkAsset.entity_id == entity.id,
                              ArtworkAsset.kind == kind)
                      .one_or_none())
            if exists is not None:
                return "skipped_invalid"  # 已有图（含 locked）不提建议
            local = _local_primary_asset(session, entity.id, self._storage)
            if local is None:
                return "skipped_invalid"  # 无本地主视频资产 → 无法自动生成（降级为 findings）
            proposal_type = "ARTWORK"
            payload = {"action": "generate_artwork", "entity_id": entity.id,
                       "kind": kind}
            payload["basis"] = {"entity_id": entity.id, "kind": kind,
                                "asset_id": local[0]}
            payload["summary"] = parts
            return self._insert(session, job_id=job_id, profile="library_curator",
                                proposal_type=proposal_type, impact_level=impact_level,
                                payload=payload)

        payload["basis"] = {"entity_id": entity.id, "fields": basis_fields}
        payload["summary"] = parts
        return self._insert(session, job_id=job_id, profile="library_curator",
                            proposal_type=proposal_type, impact_level=impact_level,
                            payload=payload)

    def _insert(self, session: Session, *, job_id, profile, proposal_type,
                impact_level, payload) -> str:
        changes_part = {k: v for k, v in payload.items() if k not in ("basis", "summary")}
        dedupe_key = _sha(f"{profile}|{proposal_type}|"
                          f"{json.dumps(changes_part, sort_keys=True, ensure_ascii=False)}")
        dupe = (session.query(AiProposal)
                .filter(AiProposal.dedupe_key == dedupe_key,
                        AiProposal.status.in_(STATUS_ACTIVE_FOR_DEDUPE))
                .first())
        if dupe is not None:
            return "skipped_duplicate"
        if self._has_pending_target(session, profile, changes_part):
            return "skipped_duplicate"
        summary = payload.get("summary") or {}
        session.add(AiProposal(
            id=new_id(), profile=profile, proposal_type=proposal_type,
            summary=_summary_text(summary), payload_json=payload,
            impact_level=impact_level, status="PENDING",
            dedupe_key=dedupe_key,
            source_context_hash=_sha(json.dumps(payload.get("basis"),
                                                sort_keys=True, ensure_ascii=False)),
            job_id=job_id,
        ))
        return "created"

    @staticmethod
    def _proposal_targets(payload: dict) -> set[str]:
        """建议触碰的目标面（字段名/关联名/artwork 位）——同实体同目标的
        未决建议视为同一条提醒。"""
        targets = set(payload.get("fields") or {})
        if payload.get("topics_add"):
            targets.add("topics")
        if payload.get("characters_add"):
            targets.add("characters")
        if payload.get("kind"):
            targets.add(f"artwork:{payload['kind']}")
        return targets

    def _has_pending_target(self, session: Session, profile: str,
                            changes_part: dict) -> bool:
        """同实体同目标面已有 PENDING → 不再新增（AIA-008 收紧：同字段不同
        值的建议每轮审计都会绕过 dedupe_key 堆积，家长处理不完；处理完现有
        建议或字段补齐后，下轮审计仍可按需再提）。"""
        targets = self._proposal_targets(changes_part)
        if not targets:
            return False
        entity_id = changes_part.get("entity_id") or ""
        rows = (session.query(AiProposal.payload_json)
                .filter(AiProposal.status == "PENDING",
                        AiProposal.profile == profile,
                        func.json_extract(AiProposal.payload_json,
                                          "$.entity_id") == entity_id)
                .all())
        return any(targets & self._proposal_targets(p or {}) for (p,) in rows)

    def create_from_advisor(self, session: Session, *, job_id: str | None,
                            kind: str, payload_parts: dict,
                            summary_parts: dict) -> str:
        """Advisor 建议创建（AIA-003/004/005）：POLICY 一律 HIGH；
        CONTENT_GAP 方向性（LOW，无应用动作）。创建期即用当前规则试合并校验
        rules_patch（垃圾补丁不落库）。"""
        parts = {
            "why": str(summary_parts.get("why") or "").strip(),
            "what": str(summary_parts.get("what") or "").strip(),
            "impact": str(summary_parts.get("impact") or "").strip(),
        }
        if not all(parts.values()):
            return "skipped_invalid"
        if kind == "POLICY":
            patch = payload_parts.get("rules_patch")
            if not isinstance(patch, dict) or not patch:
                return "skipped_invalid"
            if not set(patch) <= set(POLICY_PATCH_KEYS):
                return "skipped_invalid"
            if self._policy is None:
                return "skipped_invalid"
            rules, version = self._policy.current(session)
            from ..policy.engine import PolicyRules

            try:
                PolicyRules.parse(_deep_merge(rules.to_json(), patch))
            except (TypeError, ValueError):
                return "skipped_invalid"
            payload = {"action": "apply_policy", "entity_id": None,
                       "rules_patch": patch}
            payload["basis"] = {"policy_version": version, "window_days": 7}
            payload["summary"] = parts
            return self._insert(session, job_id=job_id, profile="family_advisor",
                                proposal_type="POLICY", impact_level="HIGH",
                                payload=payload)
        if kind == "CONTENT_GAP":
            topic = str(payload_parts.get("topic") or "").strip()
            modality = payload_parts.get("modality")
            if not topic or modality not in ("VIDEO", "AUDIO"):
                return "skipped_invalid"
            payload = {"action": "info", "entity_id": None,
                       "topic": topic, "modality": modality,
                       "language": payload_parts.get("language"),
                       "age_band": payload_parts.get("age_band")}
            payload["basis"] = {"window_days": 7}
            payload["summary"] = parts
            return self._insert(session, job_id=job_id, profile="family_advisor",
                                proposal_type="CONTENT_GAP", impact_level="LOW",
                                payload=payload)
        return "skipped_invalid"

    def _linked_names(self, session: Session, entity_id: str, *, topic: bool) -> list[str]:
        if topic:
            return [r[0] for r in (
                session.query(ContentTopic.name)
                .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
                .filter(EntityTopic.entity_id == entity_id)
                .order_by(ContentTopic.name).all())]
        return [r[0] for r in (
            session.query(ContentCharacter.name)
            .join(EntityCharacter, EntityCharacter.character_id == ContentCharacter.id)
            .filter(EntityCharacter.entity_id == entity_id)
            .order_by(ContentCharacter.name).all())]

    # ---------- 应用 / 拒绝（§19.4：重读事实 → Domain Service 执行） ----------

    def apply_one(self, session: Session, proposal_id: str) -> dict:
        row = session.get(AiProposal, proposal_id)
        if row is None:
            raise not_found("AI 建议不存在")
        if row.status != "PENDING":
            raise invalid_request(f"建议已处理（当前状态 {row.status}）")
        if row.proposal_type in ("CONTENT_GAP", "ACTIVITY"):
            raise invalid_request("该建议为方向性建议，无应用操作")
        action = (row.payload_json or {}).get("action")
        if action not in ("apply_fields", "generate_artwork", "apply_policy"):
            raise invalid_request("该建议类型不支持应用，请到对应页面操作")
        if action == "apply_fields":
            result = self._apply_metadata(session, row)
        elif action == "generate_artwork":
            result = self._apply_artwork(session, row)
        else:
            result = self._apply_policy(session, row)
        if result["status"] == "applied":
            row.status = "APPLIED"
            row.applied_at = datetime.now(UTC)
        elif result["status"] == "expired":
            row.status = "EXPIRED"
        # failed：保持 PENDING，家长可重试或手动处理
        return {"proposal_id": row.id, **result}

    def reject(self, session: Session, proposal_id: str) -> AiProposal:
        row = session.get(AiProposal, proposal_id)
        if row is None:
            raise not_found("AI 建议不存在")
        if row.status != "PENDING":
            raise invalid_request(f"建议已处理（当前状态 {row.status}）")
        row.status = "REJECTED"
        return row

    def batch_apply(self, ids: list[str], *, allow_high: bool = False) -> list[dict]:
        """批量应用：默认仅 LOW（混入 HIGH → 400 整体不执行）；allow_high=True
        为清单式一次确认（交互 v0.3.3：界面完整呈现每条前后值后一次确认），
        逐条独立事务，部分 EXPIRED 不影响其余。"""
        with self._db() as session:
            rows = (session.query(AiProposal)
                    .filter(AiProposal.id.in_(ids)).all())
            by_id = {r.id: r for r in rows}
            for pid in ids:
                row = by_id.get(pid)
                if row is None:
                    raise not_found(f"AI 建议不存在: {pid}")
                if row.status != "PENDING":
                    raise invalid_request(f"建议 {pid} 已处理（{row.status}）")
                if row.impact_level != "LOW" and not allow_high:
                    raise invalid_request(
                        "批量应用只允许低影响建议（高影响需勾选清单式确认）")
                if row.proposal_type in ("CONTENT_GAP", "ACTIVITY"):
                    raise invalid_request("方向性建议无应用操作")
        results = []
        for pid in ids:
            with self._db() as session:
                results.append(self.apply_one(session, pid))
                session.commit()
        return results

    # ---------- 执行落点（既有领域路径，A-20） ----------

    def _apply_metadata(self, session: Session, row: AiProposal) -> dict:
        payload = row.payload_json or {}
        entity = session.get(ContentEntity, payload.get("entity_id") or "")
        if entity is None:
            return {"status": "expired", "reason": "内容已被删除"}
        basis_fields = (payload.get("basis") or {}).get("fields") or {}
        for field, base in basis_fields.items():
            cur = _prov_sig(entity, field)
            if cur["locked"]:
                return {"status": "expired", "reason": f"字段「{field}」已被家长锁定"}
            if cur != base:
                return {"status": "expired",
                        "reason": f"字段「{field}」的来源已变化，建议基于旧资料"}
        fields = payload.get("fields") or {}
        for field, value in fields.items():
            if not apply_with_provenance(entity, field, value, "parent"):
                return {"status": "expired",
                        "reason": f"字段「{field}」写入被拒（锁定或来源等级更高）"}
        self._sync_media_row(session, entity, fields)
        if payload.get("topics_add"):
            self._link_names(session, entity, payload["topics_add"], topic=True)
        if payload.get("characters_add"):
            self._link_names(session, entity, payload["characters_add"], topic=False)
        return {"status": "applied",
                "applied_fields": sorted(fields.keys()),
                "note": "已按家长确认写入（来源=家长）"}

    def _sync_media_row(self, session: Session, entity: ContentEntity,
                        fields: dict) -> None:
        """应用结果回写 media 行展示字段：Admin 媒体库列表/详情与 TV 浏览的
        language/age_band 读取 media 行，不回写则家长应用建议后看不到任何
        变化。与家长手动 PATCH 同语义：parent_edited_json 记录 +
        metadata_version+1（重扫不覆盖，§7.4）。series/season 等无
        source_media_id 的实体没有对应行，跳过。
        """
        if not entity.source_media_id:
            return
        media = session.get(Media, entity.source_media_id)
        if media is None:
            return
        edited = dict(media.parent_edited_json or {})
        changed = False
        if fields.get("language"):
            media.language = fields["language"]
            edited["language"] = fields["language"]
            changed = True
        if "age_min" in fields or "age_max" in fields:
            # age_band 文本格式与 parse_age_band 可逆（'3-6'/'3+'）
            if entity.age_min is not None and entity.age_max is not None:
                band = f"{entity.age_min}-{entity.age_max}"
            elif entity.age_min is not None:
                band = f"{entity.age_min}+"
            else:
                band = None
            if band:
                media.age_band = band
                edited["age_band"] = band
                changed = True
        if changed:
            media.parent_edited_json = edited
            media.metadata_version += 1

    def _apply_artwork(self, session: Session, row: AiProposal) -> dict:
        payload = row.payload_json or {}
        basis = payload.get("basis") or {}
        kind = str(payload.get("kind") or "")
        entity = session.get(ContentEntity, payload.get("entity_id") or "")
        if entity is None:
            return {"status": "expired", "reason": "内容已被删除"}
        if kind not in ARTWORK_KINDS:
            return {"status": "expired", "reason": "建议数据不完整"}
        existing = (session.query(ArtworkAsset)
                    .filter(ArtworkAsset.entity_id == entity.id,
                            ArtworkAsset.kind == kind)
                    .one_or_none())
        if existing is not None:
            return {"status": "expired", "reason": "该类型已有图片"}
        asset = session.get(MediaAsset, basis.get("asset_id") or "")
        if asset is None or asset.missing:
            return {"status": "expired", "reason": "媒体文件已不可用"}
        if self._storage is None:
            return {"status": "failed", "reason": "存储不可用，请手动处理"}
        try:
            provider = self._storage.get(asset.mount_id)
        except Exception:
            provider = None
        if provider is None or not hasattr(provider, "abs_path"):
            return {"status": "expired", "reason": "媒体源状态已变化（非本地目录）"}
        from ..media.artwork import artwork_path, generate_from_video_frame

        if not generate_from_video_frame(self._config,
                                         provider.abs_path(asset.path_key),
                                         entity.id, kind):
            return {"status": "failed", "reason": "自动生成失败，请到刮削与匹配→Artwork 手动处理"}
        rel = str(artwork_path(self._config, entity.id, kind)
                  .relative_to(self._config.data_dir))
        session.add(ArtworkAsset(id=new_id(), entity_id=entity.id, kind=kind,
                                 source="frame", file_path=rel))
        return {"status": "applied", "note": "已从本地视频生成图片（来源=帧）"}

    def _apply_policy(self, session: Session, row: AiProposal) -> dict:
        """POLICY 应用（AC-18 / A-20）：重读当前 version → 合并补丁经
        PolicyRules 校验 → 复用 state.policy.save（version+1）+
        playback.on_policy_saved（撤销受影响 Grant 并推送 stop/deny，
        硬性约束 2 完整继承）——与页面编辑同一执行路径，Agent 无独立写入口。"""
        if self._policy is None or self._playback is None:
            return {"status": "failed", "reason": "Policy 服务不可用，请稍后重试"}
        payload = row.payload_json or {}
        basis = payload.get("basis") or {}
        patch = payload.get("rules_patch") or {}
        rules, version = self._policy.current(session)
        if basis.get("policy_version") != version:
            return {"status": "expired", "reason": "屏幕时间规则已被修改，建议基于旧版本"}
        if not isinstance(patch, dict) or not patch or not set(patch) <= set(POLICY_PATCH_KEYS):
            return {"status": "expired", "reason": "建议数据不完整或包含不允许修改的字段"}
        from ..policy.engine import PolicyRules

        merged = _deep_merge(rules.to_json(), patch)
        try:
            PolicyRules.parse(merged)
        except (TypeError, ValueError) as exc:
            return {"status": "expired",
                    "reason": f"补丁与当前规则合并后未通过校验: {exc}"}
        _new_rules, new_version = self._policy.save(session, merged)
        revoked = self._playback.on_policy_saved(session, new_version)
        return {"status": "applied", "policy_version": new_version,
                "revoked_playbacks": revoked,
                "note": "规则已保存并立即生效（与页面编辑同一执行路径）"}

    def _link_names(self, session: Session, entity: ContentEntity,
                    names: list[str], *, topic: bool) -> None:
        """追加主题/角色关联（不动既有；与 Admin PATCH 的媒体标签回写同语义）。"""
        from ..media.content_catalog import sync_tags

        existing = set(self._linked_names(session, entity.id, topic=topic))
        added = []
        for name in sorted({n.strip() for n in names if n.strip()}):
            if name in existing:
                continue
            if topic:
                topic_row = (session.query(ContentTopic)
                             .filter(ContentTopic.name == name).first())
                if topic_row is None:
                    topic_row = ContentTopic(id=new_id(), name=name)
                    session.add(topic_row)
                    session.flush()
                session.add(EntityTopic(entity_id=entity.id, topic_id=topic_row.id))
            else:
                char_row = (session.query(ContentCharacter)
                            .filter(ContentCharacter.name == name).first())
                if char_row is None:
                    char_row = ContentCharacter(id=new_id(), name=name)
                    session.add(char_row)
                    session.flush()
                session.add(EntityCharacter(entity_id=entity.id, character_id=char_row.id))
            added.append(name)
        if not added:
            return
        field = "topics" if topic else "characters"
        prov = dict(entity.meta_provenance_json or {})
        old = prov.get(field) or {}
        prov[field] = {"source": "parent", "updated_at": now_iso(),
                       "locked": bool(old.get("locked"))}
        entity.meta_provenance_json = prov
        # 叶子实体同步写回 media.tags（重扫防覆盖语义，与 _set_entity_topics 一致）
        if entity.source_media_id:
            media = session.get(Media, entity.source_media_id)
            if media is not None:
                all_names = sorted(existing | set(added))
                key = "themes" if topic else "characters"
                edited = dict(media.parent_edited_json or {})
                merged = dict(edited.get("tags") or {})
                merged[key] = all_names
                edited["tags"] = merged
                media.parent_edited_json = edited
                media.tags_json = {**(media.tags_json or {}), key: all_names}
                sync_tags(session, entity.id, media.tags_json or {})
