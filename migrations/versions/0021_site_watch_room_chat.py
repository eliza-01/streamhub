"""site watch room chat

Revision ID: 0021_site_watch_room_chat
Revises: 0020_site_watch_sessions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0021_site_watch_room_chat"
down_revision = "0020_site_watch_sessions"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def _inspect():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return name in _inspect().get_table_names()


def upgrade() -> None:
    if _table_exists("site_watch_messages"):
        return
    op.create_table(
        "site_watch_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["site_watch_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_site_watch_messages_session_id", "site_watch_messages", ["session_id"])
    op.create_index("ix_site_watch_messages_user_id", "site_watch_messages", ["user_id"])
    op.create_index("ix_site_watch_messages_created_at", "site_watch_messages", ["created_at"])
    op.create_index("ix_site_watch_messages_session_id_id", "site_watch_messages", ["session_id", "id"])
    op.create_index("ix_site_watch_messages_user_id_created_at", "site_watch_messages", ["user_id", "created_at"])


def downgrade() -> None:
    if _table_exists("site_watch_messages"):
        op.drop_table("site_watch_messages")
