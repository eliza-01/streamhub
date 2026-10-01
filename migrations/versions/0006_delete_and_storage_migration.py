"""event/video deletion lifecycle and storage root migration jobs

Revision ID: 0006_delete_storage_migration
Revises: 0005_storage_output_batches
Create Date: 2026-10-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0006_delete_storage_migration"
down_revision = "0005_storage_output_batches"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def upgrade() -> None:
    for table in ("media_events", "sessions", "video_sessions"):
        op.add_column(table, sa.Column("deletion_group_id", UUID, nullable=True))
        op.create_index(f"ix_{table}_deletion_group_id", table, ["deletion_group_id"])

    op.create_table(
        "storage_migration_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("source_root_key", sa.String(length=32), nullable=False),
        sa.Column("destination_root_key", sa.String(length=32), nullable=False),
        sa.Column("session_ids_json", sa.JSON(), nullable=True),
        sa.Column("total_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("migrated_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("copied_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("current_session_id", UUID, nullable=True),
        sa.Column("last_error", mysql.LONGTEXT(), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("started_at_utc", DT, nullable=True),
        sa.Column("completed_at_utc", DT, nullable=True),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
    )
    op.create_index("ix_storage_migration_jobs_status", "storage_migration_jobs", ["status"])
    op.create_index("ix_storage_migration_jobs_current_session_id", "storage_migration_jobs", ["current_session_id"])


def downgrade() -> None:
    op.drop_index("ix_storage_migration_jobs_current_session_id", table_name="storage_migration_jobs")
    op.drop_index("ix_storage_migration_jobs_status", table_name="storage_migration_jobs")
    op.drop_table("storage_migration_jobs")
    for table in ("video_sessions", "sessions", "media_events"):
        op.drop_index(f"ix_{table}_deletion_group_id", table_name=table)
        op.drop_column(table, "deletion_group_id")
