"""site admin display titles and persistent artwork assets

Revision ID: 0011_site_admin_assets
Revises: 0010_site_catalog
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0011_site_admin_assets"
down_revision = "0010_site_catalog"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("media_events", "display_title"):
        op.add_column("media_events", sa.Column("display_title", sa.String(length=1024), nullable=True))

    # Existing events keep their original Twitch/source title and start with the
    # same text as the editable public title.
    op.get_bind().execute(
        sa.text("UPDATE media_events SET display_title = title WHERE display_title IS NULL")
    )

    if not _table_exists("site_event_assets"):
        op.create_table(
            "site_event_assets",
            sa.Column("event_id", sa.BINARY(16), nullable=False),
            sa.Column("slot", sa.String(length=32), nullable=False),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("content_type", sa.String(length=64), nullable=False, server_default="image/webp"),
            sa.Column("size_bytes", mysql.BIGINT(unsigned=True), nullable=False),
            sa.Column("width", mysql.INTEGER(unsigned=True), nullable=False),
            sa.Column("height", mysql.INTEGER(unsigned=True), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                mysql.DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            ),
            sa.Column(
                "updated_at",
                mysql.DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            ),
            sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("event_id", "slot"),
            sa.UniqueConstraint("storage_key", name="uq_site_event_assets_storage_key"),
        )
        op.create_index("ix_site_event_assets_sha256", "site_event_assets", ["sha256"])


def downgrade() -> None:
    if _table_exists("site_event_assets"):
        op.drop_table("site_event_assets")
    if _column_exists("media_events", "display_title"):
        op.drop_column("media_events", "display_title")
