"""durable video part reservations and build queue

Revision ID: 0008_video_parts
Revises: 0007_event_session_trash
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0008_video_parts"
down_revision = "0007_event_session_trash"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _require_columns(table_name: str, expected: set[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    actual = {column["name"] for column in inspector.get_columns(table_name)}
    missing = expected - actual
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise RuntimeError(
            f"cannot resume 0008_video_parts: {table_name} exists but misses columns: {missing_csv}"
        )


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    # MySQL DDL is non-transactional. If an earlier 0008 attempt failed after
    # creating one table, Alembic still reports 0007. Make this migration
    # resumable instead of requiring a destructive manual cleanup.
    if not _table_exists("video_parts"):
        op.create_table(
            "video_parts",
            sa.Column("id", sa.BINARY(16), nullable=False),
            sa.Column("video_session_id", sa.BINARY(16), nullable=False),
            sa.Column("part_no", sa.Integer(), nullable=False),
            sa.Column("run_no", sa.Integer(), nullable=False),
            sa.Column("start_segment_no", sa.Integer(), nullable=False),
            sa.Column("end_segment_no", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("expected_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("final_bytes", sa.BigInteger(), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("relative_path", sa.String(length=1024), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.Column("completed_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.ForeignKeyConstraint(["video_session_id"], ["video_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("video_session_id", "part_no", name="uq_video_part_session_no"),
        )
    _require_columns(
        "video_parts",
        {
            "id",
            "video_session_id",
            "part_no",
            "run_no",
            "start_segment_no",
            "end_segment_no",
            "duration_ms",
            "expected_bytes",
            "final_bytes",
            "sha256",
            "file_name",
            "relative_path",
            "status",
            "last_error",
            "created_at",
            "completed_at_utc",
            "updated_at",
        },
    )
    _ensure_index("video_parts", "ix_video_parts_session", ["video_session_id"])
    _ensure_index("video_parts", "ix_video_parts_status", ["status"])

    if not _table_exists("video_part_segments"):
        op.create_table(
            "video_part_segments",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("part_id", sa.BINARY(16), nullable=False),
            # video_segments.id is BIGINT UNSIGNED on MySQL. The referencing
            # column must have the same signedness or MySQL rejects the FK.
            sa.Column(
                "segment_id",
                sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"),
                nullable=False,
            ),
            sa.Column("segment_no", sa.Integer(), nullable=False),
            sa.Column("expected_bytes", sa.BigInteger(), nullable=False),
            sa.Column("source_sha256", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["part_id"], ["video_parts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["segment_id"], ["video_segments.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("segment_id", name="uq_video_part_segment_reservation"),
            sa.UniqueConstraint("part_id", "segment_no", name="uq_video_part_segment_no"),
        )
    _require_columns(
        "video_part_segments",
        {
            "id",
            "part_id",
            "segment_id",
            "segment_no",
            "expected_bytes",
            "source_sha256",
        },
    )
    _ensure_index("video_part_segments", "ix_video_part_segments_part", ["part_id"])

    if not _table_exists("video_part_build_jobs"):
        op.create_table(
            "video_part_build_jobs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("part_id", sa.BINARY(16), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("phase", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("progress_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("heartbeat_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.Column("started_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("completed_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.ForeignKeyConstraint(["part_id"], ["video_parts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("part_id", name="uq_video_part_build_job_part"),
        )
    _require_columns(
        "video_part_build_jobs",
        {
            "id",
            "part_id",
            "status",
            "phase",
            "attempts",
            "cancel_requested",
            "progress_bytes",
            "total_bytes",
            "lease_owner",
            "lease_expires_at_utc",
            "heartbeat_at_utc",
            "last_error",
            "created_at",
            "started_at_utc",
            "completed_at_utc",
            "updated_at",
        },
    )
    _ensure_index(
        "video_part_build_jobs",
        "ix_video_part_build_jobs_status",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_part_build_jobs_status", table_name="video_part_build_jobs")
    op.drop_table("video_part_build_jobs")
    op.drop_index("ix_video_part_segments_part", table_name="video_part_segments")
    op.drop_table("video_part_segments")
    op.drop_index("ix_video_parts_status", table_name="video_parts")
    op.drop_index("ix_video_parts_session", table_name="video_parts")
    op.drop_table("video_parts")
