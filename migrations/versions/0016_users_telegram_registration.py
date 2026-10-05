"""Telegram confirmation for registered users

Revision ID: 0016_users_telegram_registration
Revises: 0015_users_email_verification
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0016_users_telegram_registration"
down_revision = "0015_users_email_verification"
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
    email_column = next(column for column in _inspect().get_columns("users") if column["name"] == "email")
    if not email_column.get("nullable", True):
        op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)

    if "telegram_user_id" not in user_columns:
        op.add_column("users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    if "telegram_username" not in user_columns:
        op.add_column("users", sa.Column("telegram_username", sa.String(length=64), nullable=True))
    if "telegram_verified_at_utc" not in user_columns:
        op.add_column("users", sa.Column("telegram_verified_at_utc", DT, nullable=True))

    if "uq_users_telegram_user_id" not in _unique_names("users"):
        op.create_unique_constraint("uq_users_telegram_user_id", "users", ["telegram_user_id"])
    if "ix_users_telegram_user_id" not in _index_names("users"):
        op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)
    if "ix_users_telegram_verified_at_utc" not in _index_names("users"):
        op.create_index("ix_users_telegram_verified_at_utc", "users", ["telegram_verified_at_utc"])

    if not _table_exists("user_telegram_registrations"):
        op.create_table(
            "user_telegram_registrations",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("start_token_hash", sa.String(64), nullable=False),
            sa.Column("browser_token_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
            sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
            sa.Column("telegram_username", sa.String(64), nullable=True),
            sa.Column("telegram_first_name", sa.String(255), nullable=True),
            sa.Column("expires_at_utc", DT, nullable=False),
            sa.Column("started_at_utc", DT, nullable=True),
            sa.Column("confirmed_at_utc", DT, nullable=True),
            sa.Column("created_at", DT, nullable=False),
            sa.Column("updated_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("start_token_hash", name="uq_user_telegram_registrations_start_token"),
            sa.UniqueConstraint("browser_token_hash", name="uq_user_telegram_registrations_browser_token"),
        )
    registration_indexes = _index_names("user_telegram_registrations")
    if "ix_user_telegram_registrations_user_id" not in registration_indexes:
        op.create_index("ix_user_telegram_registrations_user_id", "user_telegram_registrations", ["user_id"])
    if "ix_user_telegram_registrations_status" not in registration_indexes:
        op.create_index("ix_user_telegram_registrations_status", "user_telegram_registrations", ["status"])
    if "ix_user_telegram_registrations_expires_at" not in registration_indexes:
        op.create_index("ix_user_telegram_registrations_expires_at", "user_telegram_registrations", ["expires_at_utc"])
    if "ix_user_telegram_registrations_telegram_user" not in registration_indexes:
        op.create_index("ix_user_telegram_registrations_telegram_user", "user_telegram_registrations", ["telegram_user_id"])


def downgrade() -> None:
    op.drop_table("user_telegram_registrations")
    op.drop_index("ix_users_telegram_verified_at_utc", table_name="users")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_constraint("uq_users_telegram_user_id", "users", type_="unique")
    op.drop_column("users", "telegram_verified_at_utc")
    op.drop_column("users", "telegram_username")
    op.drop_column("users", "telegram_user_id")
    # Keep downgrade possible even for users created while Telegram-only registration was active.
    op.execute(sa.text(
        "UPDATE users SET email = CONCAT('telegram+', LOWER(HEX(id)), '@invalid.local') WHERE email IS NULL"
    ))
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
