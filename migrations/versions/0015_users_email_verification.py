"""registered users with email verification

Revision ID: 0015_users_email_verification
Revises: 0014_event_purge_queue
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0015_users_email_verification"
down_revision = "0014_event_purge_queue"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("users"):
        op.create_table(
            "users",
            sa.Column("id", UUID, nullable=False),
            sa.Column("nickname", sa.String(40), nullable=False),
            sa.Column("login", sa.String(32), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("avatar_path", sa.String(512), nullable=True),
            sa.Column("email_verified_at_utc", DT, nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", DT, nullable=False),
            sa.Column("updated_at", DT, nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("login", name="uq_users_login"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        op.create_index("ix_users_login", "users", ["login"], unique=True)
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_email_verified_at_utc", "users", ["email_verified_at_utc"])
        op.create_index("ix_users_is_active", "users", ["is_active"])

    if not _table_exists("user_email_verification_codes"):
        op.create_table(
            "user_email_verification_codes",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("code_hash", sa.String(64), nullable=False),
            sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("expires_at_utc", DT, nullable=False),
            sa.Column("sent_at_utc", DT, nullable=False),
            sa.Column("consumed_at_utc", DT, nullable=True),
            sa.Column("created_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_email_verification_codes_user_id", "user_email_verification_codes", ["user_id"])
        op.create_index("ix_user_email_verification_codes_expires_at_utc", "user_email_verification_codes", ["expires_at_utc"])
        op.create_index("ix_user_email_verification_codes_consumed_at_utc", "user_email_verification_codes", ["consumed_at_utc"])
        op.create_index(
            "ix_user_email_verification_user_id_id",
            "user_email_verification_codes",
            ["user_id", "id"],
        )


def downgrade() -> None:
    if _table_exists("user_email_verification_codes"):
        op.drop_table("user_email_verification_codes")
    if _table_exists("users"):
        op.drop_table("users")
