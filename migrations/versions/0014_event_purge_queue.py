"""durable Event trash purge queue

Revision ID: 0014_event_purge_queue
Revises: 0013_site_publication_visibility
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0014_event_purge_queue"
down_revision = "0013_site_publication_visibility"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _table_exists("event_purge_jobs"):
        return
    op.create_table(
        "event_purge_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.BINARY(16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("total_chat_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_video_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purged_chat_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purged_video_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("started_at_utc", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("completed_at_utc", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_purge_jobs_event_id", "event_purge_jobs", ["event_id"])
    op.create_index("ix_event_purge_jobs_status", "event_purge_jobs", ["status"])
    op.create_index("ix_event_purge_jobs_status_id", "event_purge_jobs", ["status", "id"])
    op.create_index("ix_event_purge_jobs_event_status", "event_purge_jobs", ["event_id", "status"])


def downgrade() -> None:
    if _table_exists("event_purge_jobs"):
        op.drop_table("event_purge_jobs")
