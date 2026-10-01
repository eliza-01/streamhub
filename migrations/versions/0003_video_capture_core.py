"""video capture core domain

Revision ID: 0003_video_capture_core
Revises: 0002_event_domain
Create Date: 2026-10-01

This vertical migration introduces only the Video session/run/segment/gap data
needed by the first end-to-end recorder UI. Parts/build queue arrive in the next
user-testable increment.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0003_video_capture_core"
down_revision = "0002_event_domain"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = sa.BINARY(16)


def upgrade() -> None:
    op.create_table(
        "video_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, sa.ForeignKey("media_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("completeness_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("quality", sa.String(32), nullable=False, server_default="best"),
        sa.Column("recorder_mode", sa.String(32), nullable=False, server_default="direct_hls_copy"),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("recording_started_at_utc", DT),
        sa.Column("ended_at_utc", DT),
        sa.Column("duration_recorded_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("required_start_ms", sa.BigInteger()),
        sa.Column("required_end_ms", sa.BigInteger()),
        sa.Column("coverage_start_ms", sa.BigInteger()),
        sa.Column("coverage_end_ms", sa.BigInteger()),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", mysql.LONGTEXT()),
        sa.Column("last_activity_at_utc", DT),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("metadata_json", mysql.JSON()),
        sa.Column("deleted_at_utc", DT),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        mysql_charset="utf8mb4",
    )
    for name, cols in [
        ("ix_video_sessions_event_id", ["event_id"]),
        ("ix_video_sessions_status", ["status"]),
        ("ix_video_sessions_completeness_status", ["completeness_status"]),
        ("ix_video_sessions_last_activity_at_utc", ["last_activity_at_utc"]),
        ("ix_video_sessions_deleted_at_utc", ["deleted_at_utc"]),
    ]:
        op.create_index(name, "video_sessions", cols)

    op.create_table(
        "video_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("video_session_id", UUID, sa.ForeignKey("video_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at_utc", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("ended_at_utc", DT),
        sa.Column("resume_source_offset_ms", sa.BigInteger()),
        sa.Column("first_segment_no", sa.Integer()),
        sa.Column("last_segment_no", sa.Integer()),
        sa.Column("streamlink_exit_code", sa.Integer()),
        sa.Column("ffmpeg_exit_code", sa.Integer()),
        sa.Column("close_reason", sa.String(64)),
        sa.Column("last_error", mysql.LONGTEXT()),
        sa.UniqueConstraint("video_session_id", "run_no", name="uq_video_run_session_no"),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_video_runs_video_session_id", "video_runs", ["video_session_id"])
    op.create_index("ix_video_runs_status", "video_runs", ["status"])

    op.create_table(
        "video_segments",
        sa.Column("id", sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"), primary_key=True, autoincrement=True),
        sa.Column("video_session_id", UUID, sa.ForeignKey("video_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_run_id", sa.BigInteger(), sa.ForeignKey("video_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_no", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("relative_path", sa.String(512), nullable=False),
        sa.Column("timeline_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("timeline_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_media_start_ms", sa.BigInteger()),
        sa.Column("source_media_end_ms", sa.BigInteger()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False, server_default="video/mp2t"),
        sa.Column("storage_state", sa.String(24), nullable=False, server_default="spool"),
        sa.Column("integrity_state", sa.String(24), nullable=False, server_default="size_verified"),
        sa.Column("sha256", sa.String(64)),
        sa.Column("closed_at_utc", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("video_session_id", "segment_no", name="uq_video_segment_session_no"),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_video_segments_video_session_id", "video_segments", ["video_session_id"])
    op.create_index("ix_video_segments_video_run_id", "video_segments", ["video_run_id"])
    op.create_index("ix_video_segments_storage_state", "video_segments", ["storage_state"])

    op.create_table(
        "video_gaps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("video_session_id", UUID, sa.ForeignKey("video_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("after_run_no", sa.Integer()),
        sa.Column("before_run_no", sa.Integer()),
        sa.Column("started_at_utc", DT),
        sa.Column("ended_at_utc", DT),
        sa.Column("source_start_ms", sa.BigInteger()),
        sa.Column("source_end_ms", sa.BigInteger()),
        sa.Column("reason", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_video_gaps_video_session_id", "video_gaps", ["video_session_id"])

    op.add_column("audit_log", sa.Column("event_id", UUID))
    op.add_column("audit_log", sa.Column("video_session_id", UUID))
    op.create_foreign_key("fk_audit_log_event_id", "audit_log", "media_events", ["event_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(
        "fk_audit_log_video_session_id",
        "audit_log",
        "video_sessions",
        ["video_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_log_event_id", "audit_log", ["event_id"])
    op.create_index("ix_audit_log_video_session_id", "audit_log", ["video_session_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_video_session_id", table_name="audit_log")
    op.drop_index("ix_audit_log_event_id", table_name="audit_log")
    op.drop_constraint("fk_audit_log_video_session_id", "audit_log", type_="foreignkey")
    op.drop_constraint("fk_audit_log_event_id", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "video_session_id")
    op.drop_column("audit_log", "event_id")
    for table in ["video_gaps", "video_segments", "video_runs", "video_sessions"]:
        op.drop_table(table)
