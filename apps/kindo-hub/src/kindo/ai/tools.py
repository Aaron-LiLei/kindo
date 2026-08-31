"""家长侧只读 Tool 注册表（PRD §10 家长 AI Tool 原则；AC-19）。

与 agent/tools.py（儿童 15 Tool，含 read_story）零交集、零共享代码，全部只读。家长侧任务
形态是"一次组装上下文的分析跑批"，不是儿童式多轮 Tool Calling，因此只设与
job 一一对应的复合只读工具（PRD §10 规定数据范围，不规定工具粒度）。
注册表外工具名一律拒绝（Tool Permission）；数据最小化白名单（§19.6）：
不含 path_key、凭据、儿童观看历史。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ..models import (
    ArtworkAsset,
    ContentCharacter,
    ContentEntity,
    ContentTopic,
    EntityAsset,
    EntityCharacter,
    EntityTopic,
    MediaAsset,
)
from .profiles import AgentProfile

if TYPE_CHECKING:
    from ..history.service import HistoryService
    from ..media.storage import StorageRegistry
    from ..playback.service import PlaybackService
    from ..policy.engine import PolicyEngine

logger = logging.getLogger("kindo.ai.tools")

ARTWORK_KINDS = ("poster", "backdrop", "thumbnail", "logo")

# 随 LLM 上下文暴露的 provenance 相关字段（锁定标记用于"locked 字段不生成建议"）
_LOCK_RELEVANT_FIELDS = (
    "language", "content_class", "age_min", "age_max", "overview", "topics", "characters",
)


def read_library_audit_data(session: Session, *, entity_ids: list[str],
                            storage: StorageRegistry | None = None) -> list[dict]:
    """Curator 审计数据：分批实体的 Title/Canonical+锁定标记/Match/Artwork 状态
    与本地主视频资产标记（AIA-001；无路径、无观看历史）。"""
    if not entity_ids:
        return []
    entities = (
        session.query(ContentEntity)
        .filter(ContentEntity.id.in_(entity_ids))
        .all())
    ids = [e.id for e in entities]

    topics: dict[str, list[str]] = {}
    for eid, name in (
        session.query(EntityTopic.entity_id, ContentTopic.name)
        .join(ContentTopic, ContentTopic.id == EntityTopic.topic_id)
        .filter(EntityTopic.entity_id.in_(ids))
        .order_by(ContentTopic.name).all()):
        topics.setdefault(eid, []).append(name)
    characters: dict[str, list[str]] = {}
    for eid, name in (
        session.query(EntityCharacter.entity_id, ContentCharacter.name)
        .join(ContentCharacter, ContentCharacter.id == EntityCharacter.character_id)
        .filter(EntityCharacter.entity_id.in_(ids))
        .order_by(ContentCharacter.name).all()):
        characters.setdefault(eid, []).append(name)

    artwork: dict[str, set[str]] = {}
    for eid, kind in (
        session.query(ArtworkAsset.entity_id, ArtworkAsset.kind)
        .filter(ArtworkAsset.entity_id.in_(ids)).all()):
        artwork.setdefault(eid, set()).add(kind)

    # 主视频资产（仅本地源可自动抽帧；网络源只作标记）
    local_assets: set[str] = set()
    for eid, _asset_id, mount_id in (
        session.query(EntityAsset.entity_id, EntityAsset.asset_id, MediaAsset.mount_id)
        .join(MediaAsset, MediaAsset.id == EntityAsset.asset_id)
        .filter(EntityAsset.entity_id.in_(ids),
                EntityAsset.role == "PRIMARY_VIDEO",
                MediaAsset.playable.is_(True),
                MediaAsset.missing.is_(False))
        .all()):
        if storage is not None:
            try:
                provider = storage.get(mount_id)
            except Exception:
                provider = None
            if provider is not None and hasattr(provider, "abs_path"):
                local_assets.add(eid)

    rows = []
    for e in entities:
        prov = e.meta_provenance_json or {}
        rows.append({
            "entity_id": e.id,
            "entity_type": e.entity_type,
            "title": e.title,
            "language": e.language,
            "content_class": e.content_class,
            "age_min": e.age_min,
            "age_max": e.age_max,
            "overview_present": bool(e.overview),
            "topics": topics.get(e.id, []),
            "characters": characters.get(e.id, []),
            "locked_fields": [
                f for f in _LOCK_RELEVANT_FIELDS
                if bool((prov.get(f) or {}).get("locked"))],
            "match_status": e.match_status,
            "artwork": {k: k in artwork.get(e.id, set()) for k in ARTWORK_KINDS},
            "has_local_primary_asset": e.id in local_assets,
        })
    return rows


# 注册表（只读工具；新增工具必须同时加入对应 Profile 的 tool_allowlist）
def read_family_stats(session: Session, *,
                      history: HistoryService | None = None,
                      policy: PolicyEngine | None = None,
                      playback: PlaybackService | None = None) -> dict:
    """Advisor 聚合统计（AIA-003/004/005；架构 §7.2：默认聚合而非底层
    Playback Event）。只输出分钟数/次数/主题名——无逐条观看日志
    （recent_records 不进入）、无路径、无凭据（§19.6）。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func

    from ..models import ContentEntity, ContentTopic, EntityTopic, InterestSignal, Profile, TransitionSession

    if playback is not None:
        profile_id = playback.default_profile_id(session)
    else:
        row = session.query(Profile.id).order_by(Profile.id).first()
        profile_id = row[0] if row else "default"
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    out: dict = {"window_days": 7}

    # 聚合观看（/analytics 同源口径：仅已 ACK viewing_interval）
    if history is not None:
        agg = history.analytics(session, profile_id, "week", now)
        out["viewing"] = {
            "total_minutes": agg.get("total_watched_seconds", 0) // 60,
            "by_modality_minutes": {
                k: v // 60 for k, v in (agg.get("by_modality") or {}).items()},
            "by_content_class_minutes": {
                k: v // 60 for k, v in (agg.get("by_content_class") or {}).items()},
            "top_series": [
                {"title": s.get("title"), "minutes": s.get("watched_seconds", 0) // 60}
                for s in (agg.get("top_series") or [])[:5]],
        }

    sig_filters = (
        InterestSignal.profile_id == profile_id,
        InterestSignal.created_at >= since,
        InterestSignal.created_at <= now,
    )
    topic_rows = (
        session.query(ContentTopic.name, InterestSignal.signal_type,
                      func.count(InterestSignal.id))
        .join(InterestSignal, InterestSignal.topic_id == ContentTopic.id)
        .filter(*sig_filters)
        .group_by(ContentTopic.name, InterestSignal.signal_type).all())
    topics: dict[str, dict[str, int]] = {}
    for name, stype, cnt in topic_rows:
        topics.setdefault(name, {})[stype] = cnt
    out["interest"] = {
        "topics_by_signal": topics,  # 主题 × 行为类型计数（含 asked=主动提问）
        "top_topics": [
            {"topic": n, "signals": c}
            for n, c in sorted(
                ((n, sum(d.values())) for n, d in topics.items()),
                key=lambda x: -x[1])[:10]],
    }

    ts_rows = (session.query(TransitionSession)
               .filter(TransitionSession.profile_id == profile_id,
                       TransitionSession.created_at >= since)
               .all())
    out["transition"] = {
        "total": len(ts_rows),
        "accepted": sum(1 for t in ts_rows if t.accepted),
        "rejected": sum(1 for t in ts_rows if t.rejected),
    }

    if policy is not None:
        rules, version = policy.current(session)
        out["policy"] = {"version": version, "rules": rules.to_json()}

    # 内容覆盖：主题 × 媒介实体数（InterestSignal × Catalog，AIA-005）
    cov_rows = (
        session.query(ContentTopic.name, ContentEntity.modality,
                      func.count(ContentEntity.id))
        .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
        .join(ContentEntity, ContentEntity.id == EntityTopic.entity_id)
        .group_by(ContentTopic.name, ContentEntity.modality).all())
    coverage: dict[str, dict[str, int]] = {}
    for name, modality, cnt in cov_rows:
        coverage.setdefault(name, {})[modality or "unknown"] = cnt
    mod_totals: dict[str, int] = {
        (k or "unknown"): v for k, v in (
            session.query(ContentEntity.modality, func.count(ContentEntity.id))
            .group_by(ContentEntity.modality).all())}
    out["catalog_coverage"] = {
        "topic_modality_counts": coverage,
        "library_by_modality": mod_totals,
    }
    return out


# 注册表（只读工具；新增工具必须同时加入对应 Profile 的 tool_allowlist）。
# 显式 Callable 类型：两个工具签名不同，联合推断会使 call_tool 的调用点退化为 unknown。
REGISTRY: dict[str, Callable[..., object]] = {
    "read_library_audit_data": read_library_audit_data,
    "read_family_stats": read_family_stats,
}


def tool_names() -> set[str]:
    return set(REGISTRY)


def call_tool(profile: AgentProfile, name: str, session: Session, **kwargs):
    """Tool Permission：工具必须同时在注册表与该 Profile 的 allowlist 内。"""
    if name not in REGISTRY or name not in profile.tool_allowlist:
        raise PermissionError(
            f"工具不在 Profile {profile.profile_id} 的允许清单内: {name}")
    return REGISTRY[name](session, **kwargs)
