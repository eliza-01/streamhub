"""proxy parts and manual media reclaim schema

Revision ID: 0022_proxy_parts
Revises: 0021_site_watch_room_chat
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0022_proxy_parts"
down_revision = "0021_site_watch_room_chat"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = mysql.BINARY(16)


def _inspect():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {row["name"] for row in _inspect().get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {row["name"] for row in _inspect().get_indexes(table)}


def _uniques(table: str) -> set[str]:
    return {row["name"] for row in _inspect().get_unique_constraints(table) if row.get("name")}


def _table_exists(name: str) -> bool:
    return name in _inspect().get_table_names()


def upgrade() -> None:
    part_columns = _columns("video_parts")
    with op.batch_alter_table("video_parts") as batch:
        if "kind" not in part_columns:
            batch.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="source"))
        if "local_file_state" not in part_columns:
            batch.add_column(sa.Column("local_file_state", sa.String(length=24), nullable=False, server_default="present"))
        if "local_unlinked_at_utc" not in part_columns:
            batch.add_column(sa.Column("local_unlinked_at_utc", DT, nullable=True))
        if "local_unlink_error" not in part_columns:
            batch.add_column(sa.Column("local_unlink_error", sa.Text(), nullable=True))
        if "profile_json" not in part_columns:
            batch.add_column(sa.Column("profile_json", sa.JSON(), nullable=True))

    part_indexes = _indexes("video_parts")
    if "ix_video_parts_kind" not in part_indexes:
        op.create_index("ix_video_parts_kind", "video_parts", ["kind"])
    if "ix_video_parts_local_file_state" not in part_indexes:
        op.create_index("ix_video_parts_local_file_state", "video_parts", ["local_file_state"])
    if "ix_video_parts_local_unlinked_at_utc" not in part_indexes:
        op.create_index("ix_video_parts_local_unlinked_at_utc", "video_parts", ["local_unlinked_at_utc"])

    uniques = _uniques("video_parts")
    if "uq_video_part_session_no" in uniques:
        op.drop_constraint("uq_video_part_session_no", "video_parts", type_="unique")
    uniques = _uniques("video_parts")
    if "uq_video_part_session_kind_no" not in uniques:
        op.create_unique_constraint(
            "uq_video_part_session_kind_no", "video_parts", ["video_session_id", "kind", "part_no"]
        )

    segment_columns = _columns("video_segments")
    with op.batch_alter_table("video_segments") as batch:
        if "replacement_directory" not in segment_columns:
            batch.add_column(sa.Column("replacement_directory", sa.String(length=1024), nullable=True))
        if "replacement_path" not in segment_columns:
            batch.add_column(sa.Column("replacement_path", sa.String(length=1024), nullable=True))
        if "replaced_at_utc" not in segment_columns:
            batch.add_column(sa.Column("replaced_at_utc", DT, nullable=True))
    if "ix_video_segments_replaced_at_utc" not in _indexes("video_segments"):
        op.create_index("ix_video_segments_replaced_at_utc", "video_segments", ["replaced_at_utc"])

    if not _table_exists("video_proxy_part_sources"):
        op.create_table(
            "video_proxy_part_sources",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("proxy_part_id", UUID, nullable=False),
            sa.Column("source_part_id", UUID, nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", DT, nullable=False),
            sa.ForeignKeyConstraint(["proxy_part_id"], ["video_parts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_part_id"], ["video_parts.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("proxy_part_id", "source_part_id", name="uq_video_proxy_part_source_pair"),
            sa.UniqueConstraint("source_part_id", name="uq_video_proxy_source_part"),
        )
        op.create_index("ix_video_proxy_part_sources_proxy_part_id", "video_proxy_part_sources", ["proxy_part_id"])
        op.create_index("ix_video_proxy_part_sources_source_part_id", "video_proxy_part_sources", ["source_part_id"])

    op.execute(sa.text("UPDATE video_parts SET kind='source' WHERE kind IS NULL OR kind=''"))


def downgrade() -> None:
    if _table_exists("video_proxy_part_sources"):
        op.drop_table("video_proxy_part_sources")
    uniques = _uniques("video_parts")
    if "uq_video_part_session_kind_no" in uniques:
        op.drop_constraint("uq_video_part_session_kind_no", "video_parts", type_="unique")
    if "uq_video_part_session_no" not in _uniques("video_parts"):
        op.create_unique_constraint("uq_video_part_session_no", "video_parts", ["video_session_id", "part_no"])
    for index in ("ix_video_parts_local_unlinked_at_utc", "ix_video_parts_local_file_state", "ix_video_parts_kind"):
        if index in _indexes("video_parts"):
            op.drop_index(index, table_name="video_parts")
    for column in ("profile_json", "local_unlink_error", "local_unlinked_at_utc", "local_file_state", "kind"):
        if column in _columns("video_parts"):
            op.drop_column("video_parts", column)
    if "ix_video_segments_replaced_at_utc" in _indexes("video_segments"):
        op.drop_index("ix_video_segments_replaced_at_utc", table_name="video_segments")
    for column in ("replaced_at_utc", "replacement_path", "replacement_directory"):
        if column in _columns("video_segments"):
            op.drop_column("video_segments", column)
