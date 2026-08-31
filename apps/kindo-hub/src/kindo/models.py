"""持久化实体（技术方案 §7.1）。

ConversationSession / Turn / CandidateSet / Realtime replay / 原始语音为 Ephemeral 运行态，
不在此持久化（§7.2）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import (
    true as sa_true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .util import new_id


def utcnow() -> datetime:
    return datetime.now(UTC)


class AwareDateTime(TypeDecorator):
    """AwareDateTime() 的 SQLite 实现：读出时把 naive 补成 UTC aware。"""

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):  # noqa: ARG002
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "device"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|revoked
    paired_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Profile(Base):
    __tablename__ = "profile"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), default="default")


class Series(Base):
    __tablename__ = "series"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 唯一约束防并发扫描插入同名系列（配合 scanner 的 savepoint 重试）
    title: Mapped[str] = mapped_column(String(256), unique=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Course(Base):
    __tablename__ = "course"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), unique=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Media(Base):
    __tablename__ = "media"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mount_id: Mapped[str] = mapped_column(String(64))
    path_key: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(256))
    media_type: Mapped[str] = mapped_column(String(16))  # episode|movie|lesson
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {"characters":[], "themes":[], "tags":[]}
    parent_edited_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_version: Mapped[int] = mapped_column(Integer, default=1)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    playable: Mapped[bool] = mapped_column(Boolean, default=True)
    probe_json: Mapped[dict] = mapped_column(JSON, default=dict)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    # 扫描期生成的缩略海报是否就绪（文件固定 /data/cache/posters/{id}.jpg，技术方案 §13.2）
    has_poster: Mapped[bool] = mapped_column(Boolean, default=False)
    # 自动归组（按目录结构推断）写入的合集键；sidecar/家长修正归组时为 NULL。
    # 非空表示 Episode 绑定由自动归组建立，可在后续扫描/重建中重算或解除
    auto_series_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 在线刮削结果（2026-08-21 PRD 修订：轻量 TMDB 海报刮削）。
    # {"source":"tmdb","ref_id":…,"matched_title":…,"poster_url":…,"scraped_at":…}
    scraped_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("mount_id", "path_key", name="uq_media_mount_path"),
        Index("ix_media_type_missing", "media_type", "missing"),
    )


class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), index=True)
    season_no: Mapped[int] = mapped_column(Integer, default=1)
    episode_no: Mapped[int] = mapped_column(Integer, default=1)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), unique=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)


class Lesson(Base):
    __tablename__ = "lesson"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"), index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, default=1)
    lesson_no: Mapped[int] = mapped_column(Integer, default=1)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), unique=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)


class SubtitleTrack(Base):
    __tablename__ = "subtitle_track"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_type: Mapped[str] = mapped_column(String(16))  # external|embedded
    source_ref: Mapped[str] = mapped_column(String(1024))  # Hub 内部引用，不对外
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 人类可读标签
    stat_key: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 源文件指纹（内部）
    grounding_available: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        Index("ix_subtitle_track_media", "media_id", "source_type", "source_ref"),
        UniqueConstraint("media_id", "source_type", "source_ref", name="uq_track_media_src"),
    )


class SubtitleSegment(Base):
    __tablename__ = "subtitle_segment"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("subtitle_track.id"))
    seq: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    __table_args__ = (Index("ix_segment_track_start", "track_id", "start_ms"),)


class Playback(Base):
    __tablename__ = "playback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    profile_id: Mapped[str] = mapped_column(String(36))
    media_id: Mapped[str] = mapped_column(String(36), index=True)
    # v0.3：统一内容目录维度（事件时刻快照，Policy v2 计量用；技术方案 §7.1）
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(16))  # play|resume|next|course_continue
    source: Mapped[str] = mapped_column(String(8))  # ui|ai
    state: Mapped[str] = mapped_column(String(16), default="created")
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    watched_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    audio_track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subtitle_track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (Index("ix_playback_profile_state", "profile_id", "state"),)


class PlaybackGrant(Base):
    __tablename__ = "playback_grant"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playback_id: Mapped[str] = mapped_column(String(36))
    device_id: Mapped[str] = mapped_column(String(36))
    media_id: Mapped[str] = mapped_column(String(36))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    __table_args__ = (Index("ix_grant_playback_revoked", "playback_id", "revoked_at"),)


class ViewingInterval(Base):
    __tablename__ = "viewing_interval"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playback_id: Mapped[str] = mapped_column(String(36), index=True)
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # v0.3 Policy Meter 分桶维度（技术方案 §9.2）
    content_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)


class WatchHistory(Base):
    __tablename__ = "watch_history"
    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    media_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # v0.3：断点/完成度挂 entity（目录改名不丢历史，决策二）；media_id 兼容期保留
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    last_position_ms: Mapped[int] = mapped_column(Integer, default=0)
    watched_seconds: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_watched_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class CourseProgress(Base):
    __tablename__ = "course_progress"
    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36))
    # v0.3：lesson entity id（搬迁后与新 entity 树对齐）
    lesson_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class PolicyConfig(Base):
    __tablename__ = "policy_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    rules_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON)


class ScanDirState(Base):
    """目录 mtime 增量剪枝状态（2026-08-25 优化 D）：mount_id+dir_path → mtime_ms。
    dir_path="" 的行为 last_full_scan_ms 标记。"""

    __tablename__ = "scan_dir_state"
    mount_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dir_path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    mtime_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ScanJob(Base):
    __tablename__ = "scan_job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mount_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error|interrupted
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_user"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_session"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime())
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class PairingRequest(Base):
    __tablename__ = "pairing_request"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_name: Mapped[str] = mapped_column(String(128))
    app_instance_id: Mapped[str] = mapped_column(String(128))
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    display_code: Mapped[str] = mapped_column(String(6))
    secret_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|expired|denied
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime())
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    approved_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 明文 device_token 仅在批准后、TV 首次拉取前短期保存，取走即清空（§14.2）
    pending_token: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlaybackEvent(Base):
    """event_id 去重表：保留到 playback 结束（技术方案 §4.2/§9.5）。"""
    __tablename__ = "playback_event"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playback_id: Mapped[str] = mapped_column(String(36), index=True)
    received_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class MediaMount(Base):
    """媒体来源（2026-08-25 全页面化决策：本地目录/SMB/WebDAV 统一页面管理）。

    本地：config_json={"path": 绝对路径}；网络：config_json 连接字段（无密码）+
    secret_json 写-only。storage_id：存储注册 id（默认 page-<id>；收养的配置根
    保持原根 id，媒体记录无缝）。软删除：deleted_at 置位（删除时已清除入库资源）。
    """

    __tablename__ = "media_mount"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    root_id: Mapped[str] = mapped_column(String(64), default="")
    sub_path: Mapped[str] = mapped_column(String(1024), default="")
    label: Mapped[str] = mapped_column(String(128))
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="page")
    # 网络源（PRD v0.2.3 MED-003 P0）：local | smb | webdav
    mount_type: Mapped[str] = mapped_column(String(16), default="local")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)  # 非敏感（host/share/url/username…）
    secret_json: Mapped[dict] = mapped_column(JSON, default=dict)  # 写-only（password；不回显/不进日志）
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class LlmProviderRow(Base):
    """页面添加的 LLM Provider（PRD v0.2.2 ADM-011）。

    api_key 为写-only：仅接受提交、服务端存储，任何 API 只返回
    configured/masked_hint，不进日志（技术方案 v0.2.1 §12.2）。
    """

    __tablename__ = "llm_provider"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(32), default="openai_chat_completions")
    base_url: Mapped[str] = mapped_column(String(512))
    model: Mapped[str] = mapped_column(String(128))
    api_key: Mapped[str] = mapped_column(String(512), default="")
    # 停用开关（迁移 0014）：停用=不参与会话解析/configured_count，密钥保留（区别于删除）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)


# =====================================================================
# v0.3 统一内容目录（产品基线 v0.3 决策二/三/四/七/八，技术方案 §7）
# =====================================================================

# ContentEntity 类型树：series─season─episode / course─lesson / movie / story / song
# 离屏活动不在此列（2026-08-26 双模型收敛）：活动库唯一模型 = transition_activity
# （builtin/parent/generated + preset/published/draft 审核流，决策七 7.3）。
ENTITY_TYPES = (
    "series", "season", "episode", "movie", "story", "song",
    "course", "lesson",
)
CONTENT_CLASSES = ("ENTERTAINMENT", "LEARNING", "STORY", "MUSIC", "OTHER")
MODALITIES = ("VIDEO", "AUDIO", "AI_VOICE", "OFFSCREEN")
ARTWORK_KINDS = ("poster", "backdrop", "thumbnail", "logo")
ASSET_ROLES = ("PRIMARY_VIDEO", "ALTERNATE_VIDEO", "AUDIO", "EXTRA")
# Canonical 六级合并优先级（高 → 低；技术方案 §7.5）
PROVENANCE_LEVELS = (
    "PARENT_LOCKED", "PARENT_EXPLICIT", "SIDECAR_EXPLICIT",
    "CONFIRMED_PROVIDER", "AUTO_PROVIDER", "PARSER_INFERRED",
)


class ContentEntity(Base):
    """统一内容目录实体（有媒体文件的目录项）。parent_id 自引用成树。

    离屏活动不建实体行（活动库唯一模型 = TransitionActivity，见 ENTITY_TYPES 注释）。
    """

    __tablename__ = "content_entity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_entity.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    content_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Normalizer 合并产物（决策四）：作品简介与首播/上映日期（非检索字段）
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 故事朗读文本（§7.4 story_text，sidecar 声明）：read_story 直接分句播报，
    # 不经 LLM 复述、不进入模型上下文
    story_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer, default=1)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    # 仅 series：STANDARD / EXTERNAL_EPISODE_GROUP / MANUAL（决策八）
    ordering: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 字段值来源与锁定：{field: {source, updated_at, locked}}
    meta_provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # 身份匹配状态（决策三）：none/auto/confirmed/no_match
    match_status: Mapped[str] = mapped_column(String(16), default="none")
    # Matcher 低置信时缓存的 top-3 候选（确认后清空）
    candidates_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # v0.2 兼容期与旧 media 行的映射锚点（搬迁来源，只读）
    source_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        Index("ix_entity_type_class_modality", "entity_type", "content_class", "modality"),
        Index("ix_entity_class_age", "content_class", "age_min", "age_max"),
    )


class MediaAsset(Base):
    """文件事实（v0.3 决策二）：与作品解耦，多对多经 EntityAsset 关联。"""

    __tablename__ = "media_asset"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mount_id: Mapped[str] = mapped_column(String(64))
    path_key: Mapped[str] = mapped_column(String(1024))
    file_kind: Mapped[str] = mapped_column(String(8), default="video")  # video|audio
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probe_json: Mapped[dict] = mapped_column(JSON, default=dict)
    playable: Mapped[bool] = mapped_column(Boolean, default=True)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    has_poster: Mapped[bool] = mapped_column(Boolean, default=False)
    # v0.2 兼容期与旧 media 行同 id（搬迁 1:1），旧代码经 media 表过渡
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("mount_id", "path_key", name="uq_asset_mount_path"),)


class EntityAsset(Base):
    """作品 ↔ 文件 多对多：一集多版本 / 一文件多集（role/sequence）。"""

    __tablename__ = "entity_asset"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(ForeignKey("content_entity.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_asset.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="PRIMARY_VIDEO")
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("entity_id", "asset_id", "role", name="uq_entity_asset_role"),)


class ExternalIdentity(Base):
    """稳定外部身份（决策三）：tmdb/tvdb/imdb 扩展位。"""

    __tablename__ = "external_identity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(ForeignKey("content_entity.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # tmdb|tvdb|imdb
    ref_id: Mapped[str] = mapped_column(String(64))
    matched_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    __table_args__ = (
        UniqueConstraint("entity_id", "provider", name="uq_identity_entity_provider"),
        Index("ix_identity_provider_ref", "provider", "ref_id"),
    )


class MatchDecision(Base):
    """匹配决策审计：confirmed/no_match 永不被 refresh 覆盖的依据。"""

    __tablename__ = "match_decision"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    candidate_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[str] = mapped_column(String(8))  # exact|likely|fuzzy|none
    decision: Mapped[str] = mapped_column(String(24))  # auto_apply|parent_confirm|parent_no_match|pending_saved
    decided_by: Mapped[str] = mapped_column(String(16))  # auto|parent
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class ArtworkAsset(Base):
    """实体级 Artwork（决策八）：poster/backdrop/thumbnail/logo。"""

    __tablename__ = "artwork_asset"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(ForeignKey("content_entity.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # poster|backdrop|thumbnail|logo
    source: Mapped[str] = mapped_column(String(16))  # sidecar|provider|parent|frame
    file_path: Mapped[str] = mapped_column(String(512))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("entity_id", "kind", name="uq_artwork_entity_kind"),)


class ContentTopic(Base):
    """主题树 + 别名（兴趣信号与推荐的主题权威源）。"""

    __tablename__ = "content_topic"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_topic.id"), nullable=True)
    aliases_json: Mapped[list] = mapped_column(JSON, default=list)


class EntityTopic(Base):
    __tablename__ = "entity_topic"
    entity_id: Mapped[str] = mapped_column(ForeignKey("content_entity.id"), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("content_topic.id"), primary_key=True)


class ContentCharacter(Base):
    __tablename__ = "content_character"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    aliases_json: Mapped[list] = mapped_column(JSON, default=list)


class EntityCharacter(Base):
    __tablename__ = "entity_character"
    entity_id: Mapped[str] = mapped_column(ForeignKey("content_entity.id"), primary_key=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("content_character.id"), primary_key=True)


class TransitionSession(Base):
    """成长接力业务上下文（决策七）：挂靠 Conversation，非交互状态源。"""

    __tablename__ = "transition_session"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(36), index=True)
    # 幂等锚点：profile+policy_day+limit_type+boundary_id（决策六）
    trigger_key: Mapped[str] = mapped_column(String(256), unique=True)
    trigger_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="offer")  # offer|interaction|ended
    selected_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_voice_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, index=True)


class ConversationUsage(Base):
    """常规 AI 语音对话计量（2026-08-26 工程治理：补齐 ai_voice 预算口径）。

    口径 = 会话开始 → 最后一次互动（created_at→last_activity_at）的墙钟时长，
    空闲段不计；与 transition_session.ai_voice_ms 一同汇入 ai_voice 预算消耗。
    创建即落行（crash-safe），结束时更新；重启经 finalize_orphans 收尾孤儿行。
    """

    __tablename__ = "conversation_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(64), unique=True)
    profile_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class TransitionActivity(Base):
    """离屏活动库：AI 生成一律 draft，家长 publish 才入推荐池（决策七 7.3）。"""

    __tablename__ = "transition_activity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(Text, default="")
    topics_json: Mapped[list] = mapped_column(JSON, default=list)
    age_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # builtin|parent|generated
    status: Mapped[str] = mapped_column(String(16))  # preset|published|draft
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class InterestSignal(Base):
    """客观兴趣信号（决策九）：只存引用与时间，不存文本、不写推断。"""

    __tablename__ = "interest_signal"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # watched|asked|selected|repeat|transition_joined|transition_rejected
    signal_type: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(16))  # browse|ai|transition
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, index=True)
    __table_args__ = (Index("ix_signal_profile_type_time", "profile_id", "signal_type", "created_at"),)


class AiProposal(Base):
    """家长 AI 建议统一模型（技术方案 §19.2；PRD 8.14 AIA-001~008）。

    Proposal 是内部术语，Web UI 对家长显示"AI 建议"。payload 携带 basis
    （生成时依据事实快照明文），应用时与库内事实直接逐字段比对（不重建哈希）；
    source_context_hash 仅为 basis 的一次性稳定哈希（日志对账），不参与判定。
    dedupe_key = (profile, proposal_type, 变更内容稳定序列化) 哈希（AIA-008 不重复提醒）。
    """

    __tablename__ = "ai_proposal"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile: Mapped[str] = mapped_column(String(32))  # library_curator|family_advisor
    # METADATA|POLICY|ARTWORK|CONTENT_GAP|ACTIVITY（ACTIVITY 首发预留不产生）
    proposal_type: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="")  # 家长可读三问摘要
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    impact_level: Mapped[str] = mapped_column(String(8))  # LOW|HIGH（§19.3 仅两级）
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|APPLIED|REJECTED|EXPIRED
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    source_context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, index=True)
    __table_args__ = (
        Index("ix_ai_proposal_status_impact", "status", "impact_level"),
        Index("ix_ai_proposal_profile_type", "profile", "proposal_type"),
    )


class AiJob(Base):
    """后台 AI 分析任务（技术方案 §19.5）：job_type / 状态与进度沿用 scan_job 风格；
    进程内 worker（不引入消息队列），Hub 重启把 running 标记 interrupted。"""

    __tablename__ = "ai_job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # CATALOG_AUDIT|USAGE_SUMMARY|CONTENT_COVERAGE（§19.5 当前三类）
    job_type: Mapped[str] = mapped_column(String(24), index=True)
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|interrupted
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
