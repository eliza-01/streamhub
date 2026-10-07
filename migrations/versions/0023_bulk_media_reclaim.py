"""bulk verified media reclaim

Revision ID: 0023_bulk_media_reclaim
Revises: 0022_proxy_parts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0023_bulk_media_reclaim"
down_revision = "0022_proxy_parts"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)


def _inspect():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {row["name"] for row in _inspect().get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {row["name"] for row in _inspect().get_indexes(table)}


def upgrade() -> None:
    columns = _columns("video_parts")
    with op.batch_alter_table("video_parts") as batch:
        if "external_copy_directory" not in columns:
            batch.add_column(sa.Column("external_copy_directory", sa.String(length=2048), nullable=True))
        if "external_copy_path" not in columns:
            batch.add_column(sa.Column("external_copy_path", sa.String(length=2048), nullable=True))
        if "external_copy_verified_at_utc" not in columns:
            batch.add_column(sa.Column("external_copy_verified_at_utc", DT, nullable=True))
    if "ix_video_parts_external_copy_verified_at_utc" not in _indexes("video_parts"):
        op.create_index(
            "ix_video_parts_external_copy_verified_at_utc",
            "video_parts",
            ["external_copy_verified_at_utc"],
        )


def downgrade() -> None:
    if "ix_video_parts_external_copy_verified_at_utc" in _indexes("video_parts"):
        op.drop_index("ix_video_parts_external_copy_verified_at_utc", table_name="video_parts")
    for column in ("external_copy_verified_at_utc", "external_copy_path", "external_copy_directory"):
        if column in _columns("video_parts"):
            op.drop_column("video_parts", column)
