"""telegram read-only part storage and byte offsets

Revision ID: 0009_telegram_storage
Revises: 0008_video_parts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0009_telegram_storage"
down_revision = "0008_video_parts"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _require_columns(table_name: str, expected: set[str]) -> None:
    actual = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    missing = expected - actual
    if missing:
        raise RuntimeError(
            f"cannot resume 0009_telegram_storage: {table_name} exists but misses columns: "
            + ", ".join(sorted(missing))
        )


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    existing = {
        item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)
    }
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def _backfill_part_offsets() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id,part_id,expected_bytes FROM video_part_segments "
            "ORDER BY part_id,segment_no,id"
        )
    ).mappings()
    current_part = None
    offset = 0
    updates: list[dict[str, object]] = []
    statement = sa.text(
        "UPDATE video_part_segments SET part_offset_bytes=:offset WHERE id=:id"
    )
    for row in rows:
        part_id = row["part_id"]
        if part_id != current_part:
            current_part = part_id
            offset = 0
        updates.append({"offset": offset, "id": row["id"]})
        offset += int(row["expected_bytes"] or 0)
        if len(updates) >= 1000:
            bind.execute(statement, updates)
            updates.clear()
    if updates:
        bind.execute(statement, updates)


def upgrade() -> None:
    # MySQL DDL is non-transactional. Like 0008, keep this migration resumable if
    # an earlier attempt stopped after adding the column or creating one table.
    if not _column_exists("video_part_segments", "part_offset_bytes"):
        op.add_column(
            "video_part_segments",
            sa.Column(
                "part_offset_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )
    _backfill_part_offsets()

    if not _table_exists("telegram_channel_state"):
        op.create_table(
            "telegram_channel_state",
            sa.Column("channel_id", sa.BigInteger(), nullable=False),
            sa.Column("channel_title", sa.String(length=255), nullable=True),
            sa.Column("account_id", sa.BigInteger(), nullable=True),
            sa.Column("account_display", sa.String(length=255), nullable=True),
            sa.Column(
                "last_scanned_message_id",
                mysql.BIGINT(unsigned=True),
                nullable=False,
                server_default="0",
            ),
            sa.Column("last_scan_at_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
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
            sa.PrimaryKeyConstraint("channel_id"),
        )
    _require_columns(
        "telegram_channel_state",
        {
            "channel_id",
            "channel_title",
            "account_id",
            "account_display",
            "last_scanned_message_id",
            "last_scan_at_utc",
            "last_error",
            "created_at",
            "updated_at",
        },
    )

    if not _table_exists("telegram_channel_files"):
        op.create_table(
            "telegram_channel_files",
            sa.Column("channel_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", mysql.BIGINT(unsigned=True), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("bytes", sa.BigInteger(), nullable=False),
            sa.Column("mime_type", sa.String(length=255), nullable=True),
            sa.Column("document_id", sa.BigInteger(), nullable=False),
            sa.Column("message_date_utc", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column(
                "discovered_at_utc",
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
            sa.PrimaryKeyConstraint("channel_id", "message_id"),
        )
    _require_columns(
        "telegram_channel_files",
        {
            "channel_id",
            "message_id",
            "file_name",
            "bytes",
            "mime_type",
            "document_id",
            "message_date_utc",
            "discovered_at_utc",
            "updated_at",
        },
    )
    _ensure_index(
        "telegram_channel_files",
        "ix_telegram_files_name_size",
        ["channel_id", "file_name", "bytes"],
    )
    _ensure_index(
        "telegram_channel_files",
        "ix_telegram_files_document",
        ["document_id"],
    )

    if not _table_exists("telegram_video_part_bindings"):
        op.create_table(
            "telegram_video_part_bindings",
            sa.Column("part_id", sa.BINARY(16), nullable=False),
            sa.Column("channel_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", mysql.BIGINT(unsigned=True), nullable=False),
            sa.Column(
                "matched_by",
                sa.String(length=64),
                nullable=False,
                server_default="exact_filename_size",
            ),
            sa.Column(
                "linked_at_utc",
                mysql.DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            ),
            sa.ForeignKeyConstraint(
                ["part_id"], ["video_parts.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["channel_id", "message_id"],
                [
                    "telegram_channel_files.channel_id",
                    "telegram_channel_files.message_id",
                ],
                ondelete="RESTRICT",
                name="fk_telegram_binding_file",
            ),
            sa.PrimaryKeyConstraint("part_id"),
            sa.UniqueConstraint(
                "channel_id",
                "message_id",
                name="uq_telegram_binding_message",
            ),
        )
    _require_columns(
        "telegram_video_part_bindings",
        {"part_id", "channel_id", "message_id", "matched_by", "linked_at_utc"},
    )


def downgrade() -> None:
    op.drop_table("telegram_video_part_bindings")
    op.drop_index("ix_telegram_files_document", table_name="telegram_channel_files")
    op.drop_index("ix_telegram_files_name_size", table_name="telegram_channel_files")
    op.drop_table("telegram_channel_files")
    op.drop_table("telegram_channel_state")
    op.drop_column("video_part_segments", "part_offset_bytes")
