from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import BINARY as MYSQL_BINARY, DATETIME

from .db import Base, UUIDBinary


class MediaEvent(Base):
    __tablename__ = "media_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(32), default="twitch", index=True)
    media_type: Mapped[str] = mapped_column(String(16), index=True)
    external_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    channel_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_login: Mapped[str | None] = mapped_column(String(255), index=True)
    channel_display_name: Mapped[str | None] = mapped_column(String(255))
    stream_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    video_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(1024))
    category_id: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(255))
    source_started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    source_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    related_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDBinary(), ForeignKey("media_events.id", ondelete="SET NULL"), index=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUIDBinary(), ForeignKey("media_events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), default="twitch", index=True)
    media_type: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    completeness_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    channel_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_login: Mapped[str | None] = mapped_column(String(255), index=True)
    channel_display_name: Mapped[str | None] = mapped_column(String(255))
    stream_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    video_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(1024))
    category_id: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(255))
    source_started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    recording_started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    recording_ended_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    duration_recorded_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    source_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    coverage_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    coverage_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    reconciliation_status: Mapped[str | None] = mapped_column(String(64))
    next_sequence_no: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class SessionSegment(Base):
    __tablename__ = "session_segments"
    __table_args__ = (UniqueConstraint("session_id", "segment_no", name="uq_session_segment_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    segment_no: Mapped[int] = mapped_column(Integer)
    started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    ended_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    media_offset_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    media_offset_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    timeline_offset_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    timeline_offset_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    close_reason: Mapped[str | None] = mapped_column(String(64))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_no", name="uq_chat_message_sequence"),
        UniqueConstraint("session_id", "provider_message_id", name="uq_chat_message_provider_id"),
        UniqueConstraint("session_id", "dedup_key", name="uq_chat_message_dedup_key"),
        Index("ix_chat_messages_session_timeline_id", "session_id", "timeline_offset_ms", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    provider_event_id: Mapped[str | None] = mapped_column(String(128), index=True)
    dedup_key: Mapped[bytes] = mapped_column(MYSQL_BINARY(32), index=True)
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    source_created_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    client_observed_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    server_received_at_utc: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, index=True)
    effective_message_time_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    timestamp_source: Mapped[str | None] = mapped_column(String(32))
    timestamp_accuracy_ms: Mapped[int | None] = mapped_column(Integer)
    media_offset_ms: Mapped[int | None] = mapped_column(BigInteger, index=True)
    timeline_offset_ms: Mapped[int] = mapped_column(BigInteger)
    chatter_external_id: Mapped[str | None] = mapped_column(String(64))
    chatter_login: Mapped[str | None] = mapped_column(String(255))
    chatter_name: Mapped[str | None] = mapped_column(String(255))
    color: Mapped[str | None] = mapped_column(String(16))
    badges_json: Mapped[dict | list | None] = mapped_column(JSON)
    message_text: Mapped[str] = mapped_column(Text)
    fragments_json: Mapped[dict | list | None] = mapped_column(JSON)
    reply_json: Mapped[dict | None] = mapped_column(JSON)
    bits: Mapped[int | None] = mapped_column(Integer)
    is_action: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    raw_payload_json: Mapped[dict | list | None] = mapped_column(JSON)
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)
    message_type: Mapped[str | None] = mapped_column(String(64))
    channel_points_reward_id: Mapped[str | None] = mapped_column(String(128))
    source_broadcaster_external_id: Mapped[str | None] = mapped_column(String(64))
    source_broadcaster_login: Mapped[str | None] = mapped_column(String(255))
    source_broadcaster_name: Mapped[str | None] = mapped_column(String(255))


class ChatEvent(Base):
    __tablename__ = "chat_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_no", name="uq_chat_event_sequence"),
        Index("ix_chat_events_session_timeline_id", "session_id", "timeline_offset_ms", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    timeline_offset_ms: Mapped[int] = mapped_column(BigInteger)
    provider_event_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_kind: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[dict | list | None] = mapped_column(JSON)
    created_at_utc: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_oauth_account_provider_user"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_user_id: Mapped[str] = mapped_column(String(64), index=True)
    login: Mapped[str | None] = mapped_column(String(255))
    scopes_json: Mapped[list | None] = mapped_column(JSON)
    validated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    oauth_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("oauth_accounts.id", ondelete="CASCADE"), index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    replaced_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class CaptureJob(Base):
    __tablename__ = "capture_jobs"
    __table_args__ = (UniqueConstraint("session_id", "job_kind", name="uq_capture_job_session_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    job_kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    checkpoint_cursor: Mapped[str | None] = mapped_column(Text)
    last_offset_ms: Mapped[int | None] = mapped_column(BigInteger)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    messages_processed: Mapped[int] = mapped_column(BigInteger, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)


class VideoSession(Base):
    __tablename__ = "video_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("media_events.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    completeness_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    quality: Mapped[str] = mapped_column(String(32), default="best")
    recorder_mode: Mapped[str] = mapped_column(String(32), default="direct_hls_copy")
    source_url: Mapped[str] = mapped_column(String(2048))
    recording_started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    ended_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    duration_recorded_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    required_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    required_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    coverage_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    coverage_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_activity_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoRun(Base):
    __tablename__ = "video_runs"
    __table_args__ = (UniqueConstraint("video_session_id", "run_no", name="uq_video_run_session_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("video_sessions.id", ondelete="CASCADE"), index=True)
    run_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at_utc: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    ended_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    resume_source_offset_ms: Mapped[int | None] = mapped_column(BigInteger)
    first_segment_no: Mapped[int | None] = mapped_column(Integer)
    last_segment_no: Mapped[int | None] = mapped_column(Integer)
    streamlink_exit_code: Mapped[int | None] = mapped_column(Integer)
    ffmpeg_exit_code: Mapped[int | None] = mapped_column(Integer)
    close_reason: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)


class VideoSegment(Base):
    __tablename__ = "video_segments"
    __table_args__ = (UniqueConstraint("video_session_id", "segment_no", name="uq_video_segment_session_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("video_sessions.id", ondelete="CASCADE"), index=True)
    video_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("video_runs.id", ondelete="CASCADE"), index=True)
    segment_no: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(String(512))
    timeline_start_ms: Mapped[int] = mapped_column(BigInteger)
    timeline_end_ms: Mapped[int] = mapped_column(BigInteger)
    source_media_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    source_media_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(64), default="video/mp2t")
    storage_state: Mapped[str] = mapped_column(String(24), default="spool", index=True)
    integrity_state: Mapped[str] = mapped_column(String(24), default="size_verified")
    sha256: Mapped[str | None] = mapped_column(String(64))
    archive_attempts: Mapped[int] = mapped_column(Integer, default=0)
    archive_last_error: Mapped[str | None] = mapped_column(Text)
    archived_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    closed_at_utc: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoGap(Base):
    __tablename__ = "video_gaps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_session_id: Mapped[uuid.UUID] = mapped_column(UUIDBinary(), ForeignKey("video_sessions.id", ondelete="CASCADE"), index=True)
    after_run_no: Mapped[int | None] = mapped_column(Integer)
    before_run_no: Mapped[int | None] = mapped_column(Integer)
    started_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    ended_at_utc: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    source_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    source_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(64), default="unknown")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class StorageOutputSetting(Base):
    __tablename__ = "storage_output_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    output_root_key: Mapped[str] = mapped_column(String(32), default="root1")
    output_subdir: Mapped[str] = mapped_column(String(512), default="streamhub")
    batch_segments: Mapped[int] = mapped_column(Integer, default=100)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBinary(), ForeignKey("media_events.id", ondelete="SET NULL"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBinary(), ForeignKey("sessions.id", ondelete="SET NULL"), index=True)
    video_session_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBinary(), ForeignKey("video_sessions.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow)
