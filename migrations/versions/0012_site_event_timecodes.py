"""event timecodes for StreamVault public playback

Revision ID: 0012_site_event_timecodes
Revises: 0011_site_admin_assets
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0012_site_event_timecodes"
down_revision = "0011_site_admin_assets"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _table_exists("site_event_timecodes"):
        return
    op.create_table(
        "site_event_timecodes",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.BINARY(16), nullable=False),
        sa.Column("position", mysql.SMALLINT(unsigned=True), nullable=False, server_default="1"),
        sa.Column("offset_ms", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "position", name="uq_site_event_timecodes_position"),
    )
    op.create_index(
        "ix_site_event_timecodes_event_offset",
        "site_event_timecodes",
        ["event_id", "offset_ms"],
    )


def downgrade() -> None:
    if _table_exists("site_event_timecodes"):
        op.drop_table("site_event_timecodes")
