"""site watch sessions

Revision ID: 0020_site_watch_sessions
Revises: 0019_comment_threads_reactions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0020_site_watch_sessions"
down_revision = "0019_comment_threads_reactions"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def _inspect():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return name in _inspect().get_table_names()


def upgrade() -> None:
    if not _table_exists("site_watch_sessions"):
        op.create_table(
            "site_watch_sessions",
            sa.Column("id", UUID, nullable=False),
            sa.Column("event_id", UUID, nullable=False),
            sa.Column("owner_user_id", UUID, nullable=True),
            sa.Column("owner_client_id", sa.String(length=64), nullable=False),
            sa.Column("owner_label", sa.String(length=64), nullable=False),
            sa.Column("position_ms", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("is_playing", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("state_version", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("state_updated_at", DT, nullable=False),
            sa.Column("last_activity_at", DT, nullable=False),
            sa.Column("closed_at", DT, nullable=True),
            sa.Column("created_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_site_watch_sessions_event_id", "site_watch_sessions", ["event_id"])
        op.create_index("ix_site_watch_sessions_owner_user_id", "site_watch_sessions", ["owner_user_id"])
        op.create_index("ix_site_watch_sessions_last_activity_at", "site_watch_sessions", ["last_activity_at"])
        op.create_index("ix_site_watch_sessions_closed_at", "site_watch_sessions", ["closed_at"])
        op.create_index("ix_site_watch_sessions_event_activity", "site_watch_sessions", ["event_id", "last_activity_at"])

    if not _table_exists("site_watch_participants"):
        op.create_table(
            "site_watch_participants",
            sa.Column("id", UUID, nullable=False),
            sa.Column("session_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=True),
            sa.Column("client_id", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=64), nullable=False),
            sa.Column("joined_at", DT, nullable=False),
            sa.Column("last_seen_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["site_watch_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", "client_id", name="uq_site_watch_participants_session_client"),
        )
        op.create_index("ix_site_watch_participants_session_id", "site_watch_participants", ["session_id"])
        op.create_index("ix_site_watch_participants_user_id", "site_watch_participants", ["user_id"])
        op.create_index("ix_site_watch_participants_last_seen_at", "site_watch_participants", ["last_seen_at"])
        op.create_index("ix_site_watch_participants_session_seen", "site_watch_participants", ["session_id", "last_seen_at"])


def downgrade() -> None:
    if _table_exists("site_watch_participants"):
        op.drop_table("site_watch_participants")
    if _table_exists("site_watch_sessions"):
        op.drop_table("site_watch_sessions")
