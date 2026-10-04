"""manual StreamVault publication visibility

Revision ID: 0013_site_publication_visibility
Revises: 0012_site_event_timecodes
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0013_site_publication_visibility"
down_revision = "0012_site_event_timecodes"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _column_exists("site_event_publications", "hidden_at_utc"):
        op.add_column(
            "site_event_publications",
            sa.Column("hidden_at_utc", mysql.DATETIME(fsp=6), nullable=True),
        )
    if not _index_exists("site_event_publications", "ix_site_event_publications_hidden"):
        op.create_index(
            "ix_site_event_publications_hidden",
            "site_event_publications",
            ["hidden_at_utc"],
        )


def downgrade() -> None:
    if _index_exists("site_event_publications", "ix_site_event_publications_hidden"):
        op.drop_index("ix_site_event_publications_hidden", table_name="site_event_publications")
    if _column_exists("site_event_publications", "hidden_at_utc"):
        op.drop_column("site_event_publications", "hidden_at_utc")
