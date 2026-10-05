"""Twitch registration and comment text colors

Revision ID: 0018_twitch_registration_comment_color
Revises: 0017_site_comments
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0018_twitch_registration_comment_color"
down_revision = "0017_site_comments"
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
    user_columns = _column_names("users")
    if "twitch_user_id" not in user_columns:
        op.add_column("users", sa.Column("twitch_user_id", sa.String(length=64), nullable=True))
    if "twitch_login" not in user_columns:
        op.add_column("users", sa.Column("twitch_login", sa.String(length=255), nullable=True))
    if "twitch_verified_at_utc" not in user_columns:
        op.add_column("users", sa.Column("twitch_verified_at_utc", DT, nullable=True))

    if "uq_users_twitch_user_id" not in _unique_names("users"):
        op.create_unique_constraint("uq_users_twitch_user_id", "users", ["twitch_user_id"])
    if "ix_users_twitch_user_id" not in _index_names("users"):
        op.create_index("ix_users_twitch_user_id", "users", ["twitch_user_id"], unique=True)
    if "ix_users_twitch_verified_at_utc" not in _index_names("users"):
        op.create_index("ix_users_twitch_verified_at_utc", "users", ["twitch_verified_at_utc"])

    if not _table_exists("user_twitch_registrations"):
        op.create_table(
            "user_twitch_registrations",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("state_hash", sa.String(length=64), nullable=False),
            sa.Column("browser_token_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("twitch_user_id", sa.String(length=64), nullable=True),
            sa.Column("twitch_login", sa.String(length=255), nullable=True),
            sa.Column("expires_at_utc", DT, nullable=False),
            sa.Column("confirmed_at_utc", DT, nullable=True),
            sa.Column("created_at", DT, nullable=False),
            sa.Column("updated_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("state_hash", name="uq_user_twitch_registrations_state"),
            sa.UniqueConstraint("browser_token_hash", name="uq_user_twitch_registrations_browser_token"),
        )
    registration_indexes = _index_names("user_twitch_registrations")
    if "ix_user_twitch_registrations_user_id" not in registration_indexes:
        op.create_index("ix_user_twitch_registrations_user_id", "user_twitch_registrations", ["user_id"])
    if "ix_user_twitch_registrations_status" not in registration_indexes:
        op.create_index("ix_user_twitch_registrations_status", "user_twitch_registrations", ["status"])
    if "ix_user_twitch_registrations_expires_at" not in registration_indexes:
        op.create_index("ix_user_twitch_registrations_expires_at", "user_twitch_registrations", ["expires_at_utc"])
    if "ix_user_twitch_registrations_twitch_user" not in registration_indexes:
        op.create_index("ix_user_twitch_registrations_twitch_user", "user_twitch_registrations", ["twitch_user_id"])

    if "text_color" not in _column_names("site_comments"):
        op.add_column(
            "site_comments",
            sa.Column("text_color", sa.String(length=7), nullable=False, server_default="#ff9b37"),
        )


def downgrade() -> None:
    op.drop_column("site_comments", "text_color")
    op.drop_table("user_twitch_registrations")
    op.drop_index("ix_users_twitch_verified_at_utc", table_name="users")
    op.drop_index("ix_users_twitch_user_id", table_name="users")
    op.drop_constraint("uq_users_twitch_user_id", "users", type_="unique")
    op.drop_column("users", "twitch_verified_at_utc")
    op.drop_column("users", "twitch_login")
    op.drop_column("users", "twitch_user_id")
