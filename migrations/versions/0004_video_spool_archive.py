"""video spool to archive handoff

Revision ID: 0004_video_spool_archive
Revises: 0003_video_capture_core
Create Date: 2026-10-01

Adds durable archive handoff diagnostics to video_segments. Existing Stage 2
segments remain in storage_state=spool and are picked up idempotently by the
video-recorder storage worker after deploy.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0004_video_spool_archive"
down_revision = "0003_video_capture_core"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.add_column(
        "video_segments",
        sa.Column("archive_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("video_segments", sa.Column("archive_last_error", mysql.LONGTEXT()))
    op.add_column("video_segments", sa.Column("archived_at_utc", DT))
    op.create_index("ix_video_segments_archived_at_utc", "video_segments", ["archived_at_utc"])


def downgrade() -> None:
    op.drop_index("ix_video_segments_archived_at_utc", table_name="video_segments")
    op.drop_column("video_segments", "archived_at_utc")
    op.drop_column("video_segments", "archive_last_error")
    op.drop_column("video_segments", "archive_attempts")
