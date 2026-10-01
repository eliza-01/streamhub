"""initial StreamHub schema

Revision ID: 0001_initial
Revises: None
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = sa.BINARY(16)


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False, server_default="twitch"),
        sa.Column("media_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("completeness_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("channel_external_id", sa.String(64)),
        sa.Column("channel_login", sa.String(255)),
        sa.Column("channel_display_name", sa.String(255)),
        sa.Column("stream_external_id", sa.String(64)),
        sa.Column("video_external_id", sa.String(64)),
        sa.Column("title", sa.String(1024)),
        sa.Column("category_id", sa.String(64)),
        sa.Column("category_name", sa.String(255)),
        sa.Column("source_started_at_utc", DT),
        sa.Column("recording_started_at_utc", DT),
        sa.Column("recording_ended_at_utc", DT),
        sa.Column("duration_recorded_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source_duration_ms", sa.BigInteger()),
        sa.Column("deleted_at_utc", DT),
        sa.Column("metadata_json", mysql.JSON()),
        sa.Column("coverage_start_ms", sa.BigInteger()),
        sa.Column("coverage_end_ms", sa.BigInteger()),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reconciliation_status", sa.String(64)),
        sa.Column("next_sequence_no", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        mysql_charset="utf8mb4",
    )
    for name, col in [
        ("ix_sessions_platform", "platform"), ("ix_sessions_media_type", "media_type"),
        ("ix_sessions_status", "status"), ("ix_sessions_completeness_status", "completeness_status"),
        ("ix_sessions_channel_external_id", "channel_external_id"), ("ix_sessions_channel_login", "channel_login"),
        ("ix_sessions_stream_external_id", "stream_external_id"), ("ix_sessions_video_external_id", "video_external_id"),
        ("ix_sessions_deleted_at_utc", "deleted_at_utc")]:
        op.create_index(name, "sessions", [col])

    op.create_table(
        "session_segments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_no", sa.Integer(), nullable=False),
        sa.Column("started_at_utc", DT), sa.Column("ended_at_utc", DT),
        sa.Column("media_offset_start_ms", sa.BigInteger()), sa.Column("media_offset_end_ms", sa.BigInteger()),
        sa.Column("timeline_offset_start_ms", sa.BigInteger()), sa.Column("timeline_offset_end_ms", sa.BigInteger()),
        sa.Column("close_reason", sa.String(64)),
        sa.UniqueConstraint("session_id", "segment_no", name="uq_session_segment_no"),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_session_segments_session_id", "session_segments", ["session_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("provider_message_id", sa.String(128)), sa.Column("provider_event_id", sa.String(128)),
        sa.Column("dedup_key", sa.BINARY(32), nullable=False), sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_created_at_utc", DT), sa.Column("client_observed_at_utc", DT),
        sa.Column("server_received_at_utc", DT, nullable=False), sa.Column("effective_message_time_utc", DT),
        sa.Column("timestamp_source", sa.String(32)), sa.Column("timestamp_accuracy_ms", sa.Integer()),
        sa.Column("media_offset_ms", sa.BigInteger()), sa.Column("timeline_offset_ms", sa.BigInteger(), nullable=False),
        sa.Column("chatter_external_id", sa.String(64)), sa.Column("chatter_login", sa.String(255)),
        sa.Column("chatter_name", sa.String(255)), sa.Column("color", sa.String(16)),
        sa.Column("badges_json", mysql.JSON()), sa.Column("message_text", mysql.LONGTEXT(), nullable=False),
        sa.Column("fragments_json", mysql.JSON()), sa.Column("reply_json", mysql.JSON()),
        sa.Column("bits", sa.Integer()), sa.Column("is_action", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")), sa.Column("deleted_at_utc", DT),
        sa.Column("raw_payload_json", mysql.JSON()), sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("message_type", sa.String(64)), sa.Column("channel_points_reward_id", sa.String(128)),
        sa.Column("source_broadcaster_external_id", sa.String(64)), sa.Column("source_broadcaster_login", sa.String(255)),
        sa.Column("source_broadcaster_name", sa.String(255)),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_chat_message_sequence"),
        sa.UniqueConstraint("session_id", "provider_message_id", name="uq_chat_message_provider_id"),
        sa.UniqueConstraint("session_id", "dedup_key", name="uq_chat_message_dedup_key"),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_provider_event_id", "chat_messages", ["provider_event_id"])
    op.create_index("ix_chat_messages_dedup_key", "chat_messages", ["dedup_key"])
    op.create_index("ix_chat_messages_source_kind", "chat_messages", ["source_kind"])
    op.create_index("ix_chat_messages_server_received_at_utc", "chat_messages", ["server_received_at_utc"])
    op.create_index("ix_chat_messages_effective_message_time_utc", "chat_messages", ["effective_message_time_utc"])
    op.create_index("ix_chat_messages_media_offset_ms", "chat_messages", ["media_offset_ms"])
    op.create_index("ix_chat_messages_is_deleted", "chat_messages", ["is_deleted"])
    op.create_index("ix_chat_messages_session_timeline_id", "chat_messages", ["session_id", "timeline_offset_ms", "id"])

    op.create_table(
        "chat_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False), sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("timeline_offset_ms", sa.BigInteger(), nullable=False), sa.Column("provider_event_id", sa.String(128)),
        sa.Column("source_kind", sa.String(32)), sa.Column("payload_json", mysql.JSON()),
        sa.Column("created_at_utc", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_chat_event_sequence"), mysql_charset="utf8mb4",
    )
    op.create_index("ix_chat_events_session_id", "chat_events", ["session_id"])
    op.create_index("ix_chat_events_event_type", "chat_events", ["event_type"])
    op.create_index("ix_chat_events_provider_event_id", "chat_events", ["provider_event_id"])
    op.create_index("ix_chat_events_session_timeline_id", "chat_events", ["session_id", "timeline_offset_ms", "id"])

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_user_id", sa.String(64), nullable=False), sa.Column("login", sa.String(255)),
        sa.Column("scopes_json", mysql.JSON()), sa.Column("validated_at", DT),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_account_provider_user"), mysql_charset="utf8mb4",
    )
    op.create_index("ix_oauth_accounts_provider", "oauth_accounts", ["provider"])
    op.create_index("ix_oauth_accounts_provider_user_id", "oauth_accounts", ["provider_user_id"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("oauth_account_id", sa.BigInteger(), sa.ForeignKey("oauth_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("access_token_encrypted", mysql.LONGTEXT(), nullable=False), sa.Column("refresh_token_encrypted", mysql.LONGTEXT()),
        sa.Column("expires_at", DT), sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("replaced_at", DT), mysql_charset="utf8mb4",
    )
    op.create_index("ix_auth_tokens_oauth_account_id", "auth_tokens", ["oauth_account_id"])

    op.create_table(
        "capture_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_kind", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("checkpoint_cursor", mysql.LONGTEXT()), sa.Column("last_offset_ms", sa.BigInteger()),
        sa.Column("pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_processed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_error", mysql.LONGTEXT()),
        sa.Column("metadata_json", mysql.JSON()),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("session_id", "job_kind", name="uq_capture_job_session_kind"), mysql_charset="utf8mb4",
    )
    op.create_index("ix_capture_jobs_session_id", "capture_jobs", ["session_id"])
    op.create_index("ix_capture_jobs_job_kind", "capture_jobs", ["job_kind"])
    op.create_index("ix_capture_jobs_status", "capture_jobs", ["status"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(64), nullable=False), sa.Column("payload_json", mysql.JSON()),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")), mysql_charset="utf8mb4",
    )
    op.create_index("ix_audit_log_session_id", "audit_log", ["session_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])


def downgrade() -> None:
    for table in ["audit_log", "capture_jobs", "auth_tokens", "oauth_accounts", "chat_events", "chat_messages", "session_segments", "sessions"]:
        op.drop_table(table)
