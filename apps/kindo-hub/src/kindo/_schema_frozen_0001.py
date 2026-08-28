"""迁移 0001 的冻结 Schema 快照（2026-08-19 / 基线 0003 时的 kindo.models 一比一副本）。

0001 不得 import 运行时的 kindo.models——那会让新装库的初始 schema 随模型演进
漂移（迁移反模式）。模型变更一律走新增迁移；此文件只在需要重建"历史某个版本
的初始 schema"时才同步修改（正常情况下永不修改）。
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    title: Mapped[str] = mapped_column(String(256))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Course(Base):
    __tablename__ = "course"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
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
    tags_json: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_edited_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_version: Mapped[int] = mapped_column(Integer, default=1)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    playable: Mapped[bool] = mapped_column(Boolean, default=True)
    probe_json: Mapped[dict] = mapped_column(JSON, default=dict)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("mount_id", "path_key", name="uq_media_mount_path"),)


class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"))
    season_no: Mapped[int] = mapped_column(Integer, default=1)
    episode_no: Mapped[int] = mapped_column(Integer, default=1)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), unique=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)


class Lesson(Base):
    __tablename__ = "lesson"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
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
    source_ref: Mapped[str] = mapped_column(String(1024))
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stat_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    device_id: Mapped[str] = mapped_column(String(36))
    profile_id: Mapped[str] = mapped_column(String(36))
    media_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(16), default="created")
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    watched_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
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


class ViewingInterval(Base):
    __tablename__ = "viewing_interval"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playback_id: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(AwareDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class WatchHistory(Base):
    __tablename__ = "watch_history"
    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    media_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_position_ms: Mapped[int] = mapped_column(Integer, default=0)
    watched_seconds: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_watched_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class CourseProgress(Base):
    __tablename__ = "course_progress"
    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36))
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


class ScanJob(Base):
    __tablename__ = "scan_job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mount_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="queued")
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
    state: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime())
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    approved_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pending_token: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlaybackEvent(Base):
    __tablename__ = "playback_event"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playback_id: Mapped[str] = mapped_column(String(36))
    received_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)


class MediaMount(Base):
    __tablename__ = "media_mount"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_id: Mapped[str] = mapped_column(String(64), default="")
    sub_path: Mapped[str] = mapped_column(String(1024), default="")
    label: Mapped[str] = mapped_column(String(128))
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="page")
    mount_type: Mapped[str] = mapped_column(String(16), default="local")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class LlmProviderRow(Base):
    __tablename__ = "llm_provider"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(32), default="openai_chat_completions")
    base_url: Mapped[str] = mapped_column(String(512))
    model: Mapped[str] = mapped_column(String(128))
    api_key: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=utcnow, onupdate=utcnow)
