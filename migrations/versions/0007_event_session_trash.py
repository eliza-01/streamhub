"""make MediaEvent a durable identity and keep Trash session-scoped

Revision ID: 0007_event_session_trash
Revises: 0006_delete_storage_migration
Create Date: 2026-10-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_event_session_trash"
down_revision = "0006_delete_storage_migration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older Stage 5 builds put the canonical MediaEvent shell itself in Trash.
    # From this revision onward only Chat/Video sessions have a trash lifecycle.
    # Child deleted_at/deletion_group_id values are deliberately preserved.
    op.execute(
        sa.text(
            "UPDATE media_events "
            "SET deleted_at_utc = NULL, deletion_group_id = NULL "
            "WHERE deleted_at_utc IS NOT NULL OR deletion_group_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    # The previous shell-level deletion state cannot be reconstructed safely
    # from session rows because several deletion batches may share one Event.
    pass
