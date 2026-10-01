"""storage output selection and batched spool handoff

Revision ID: 0005_storage_output_batches
Revises: 0004_video_spool_archive
Create Date: 2026-10-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0005_storage_output_batches"
down_revision = "0004_video_spool_archive"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_table(
        "storage_output_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("output_root_key", sa.String(length=32), nullable=False, server_default="root1"),
        sa.Column("output_subdir", sa.String(length=512), nullable=False, server_default="streamhub"),
        sa.Column("batch_segments", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
    )
    op.execute(
        sa.text(
            "insert into storage_output_settings (id, output_root_key, output_subdir, batch_segments) "
            "values (1, 'root1', 'streamhub', 100)"
        )
    )


def downgrade() -> None:
    op.drop_table("storage_output_settings")
