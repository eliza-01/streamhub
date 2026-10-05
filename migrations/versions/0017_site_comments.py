"""site user comments

Revision ID: 0017_site_comments
Revises: 0016_users_telegram_registration
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0017_site_comments"
down_revision = "0016_users_telegram_registration"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def _inspect():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return name in _inspect().get_table_names()


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in _inspect().get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {index["name"] for index in _inspect().get_indexes(table)}


def _unique_names(table: str) -> set[str]:
    return {item["name"] for item in _inspect().get_unique_constraints(table) if item.get("name")}


def upgrade() -> None:
    if not _table_exists("site_comments"):
        op.create_table(
            "site_comments",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("event_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = _index_names("site_comments")
    if "ix_site_comments_event_id" not in indexes:
        op.create_index("ix_site_comments_event_id", "site_comments", ["event_id"])
    if "ix_site_comments_user_id" not in indexes:
        op.create_index("ix_site_comments_user_id", "site_comments", ["user_id"])
    if "ix_site_comments_created_at" not in indexes:
        op.create_index("ix_site_comments_created_at", "site_comments", ["created_at"])
    if "ix_site_comments_event_id_id" not in indexes:
        op.create_index("ix_site_comments_event_id_id", "site_comments", ["event_id", "id"])
    if "ix_site_comments_user_id_created_at" not in indexes:
        op.create_index("ix_site_comments_user_id_created_at", "site_comments", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("site_comments")
