"""comment replies, underline and reactions

Revision ID: 0019_comment_threads_reactions
Revises: 0018_twitch_registration_comment_color
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0019_comment_threads_reactions"
down_revision = "0018_twitch_registration_comment_color"
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


def _foreign_key_names(table: str) -> set[str]:
    return {item["name"] for item in _inspect().get_foreign_keys(table) if item.get("name")}


def upgrade() -> None:
    columns = _column_names("site_comments")
    if "parent_comment_id" not in columns:
        op.add_column("site_comments", sa.Column("parent_comment_id", sa.BigInteger(), nullable=True))
    if "is_underlined" not in columns:
        op.add_column(
            "site_comments",
            sa.Column("is_underlined", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    indexes = _index_names("site_comments")
    if "ix_site_comments_parent_comment_id" not in indexes:
        op.create_index("ix_site_comments_parent_comment_id", "site_comments", ["parent_comment_id"])
    if "fk_site_comments_parent_comment_id" not in _foreign_key_names("site_comments"):
        op.create_foreign_key(
            "fk_site_comments_parent_comment_id",
            "site_comments",
            "site_comments",
            ["parent_comment_id"],
            ["id"],
            ondelete="CASCADE",
        )

    if not _table_exists("site_comment_reactions"):
        op.create_table(
            "site_comment_reactions",
            sa.Column("comment_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("reaction", sa.String(length=24), nullable=False),
            sa.Column("created_at", DT, nullable=False),
            sa.Column("updated_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["comment_id"], ["site_comments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("comment_id", "user_id"),
        )
    reaction_indexes = _index_names("site_comment_reactions")
    if "ix_site_comment_reactions_user_id" not in reaction_indexes:
        op.create_index("ix_site_comment_reactions_user_id", "site_comment_reactions", ["user_id"])


def downgrade() -> None:
    op.drop_table("site_comment_reactions")
    op.drop_constraint("fk_site_comments_parent_comment_id", "site_comments", type_="foreignkey")
    op.drop_index("ix_site_comments_parent_comment_id", table_name="site_comments")
    op.drop_column("site_comments", "is_underlined")
    op.drop_column("site_comments", "parent_comment_id")
