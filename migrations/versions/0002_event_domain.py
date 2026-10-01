"""media event domain and chat-session association

Revision ID: 0002_event_domain
Revises: 0001_initial
Create Date: 2026-10-01

This vertical migration intentionally introduces only the event association that
is exposed by the Stage 1 UI. Video tables arrive with the first testable Video
increment instead of landing as unexercised backend-only schema.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0002_event_domain"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

DT = mysql.DATETIME(fsp=6)
UUID = sa.BINARY(16)


def _uuid_text(raw: bytes) -> str:
    return str(uuid.UUID(bytes=bytes(raw)))


def _external_key(row: dict[str, Any]) -> str:
    platform = (row.get("platform") or "twitch").lower()
    media_type = row.get("media_type")
    if media_type == "vod" and row.get("video_external_id"):
        return f"{platform}:vod:{row['video_external_id']}"
    if media_type == "live" and row.get("stream_external_id"):
        return f"{platform}:live:{row['stream_external_id']}"
    return f"legacy:{_uuid_text(row['id'])}"


def _first_non_null(rows: list[dict[str, Any]], field: str) -> Any:
    for row in reversed(rows):
        value = row.get(field)
        if value not in (None, ""):
            return value
    return None


def _source_url(rows: list[dict[str, Any]], media_type: str) -> str | None:
    video_id = _first_non_null(rows, "video_external_id")
    channel_login = _first_non_null(rows, "channel_login")
    if media_type == "vod" and video_id:
        return f"https://www.twitch.tv/videos/{video_id}"
    if media_type == "live" and channel_login:
        return f"https://www.twitch.tv/{channel_login}"
    for row in reversed(rows):
        metadata = row.get("metadata_json")
        if isinstance(metadata, dict) and metadata.get("page_url"):
            return metadata["page_url"]
    return None


def upgrade() -> None:
    op.create_table(
        "media_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False, server_default="twitch"),
        sa.Column("media_type", sa.String(16), nullable=False),
        sa.Column("external_key", sa.String(255), nullable=False),
        sa.Column("channel_external_id", sa.String(64)),
        sa.Column("channel_login", sa.String(255)),
        sa.Column("channel_display_name", sa.String(255)),
        sa.Column("stream_external_id", sa.String(64)),
        sa.Column("video_external_id", sa.String(64)),
        sa.Column("title", sa.String(1024)),
        sa.Column("category_id", sa.String(64)),
        sa.Column("category_name", sa.String(255)),
        sa.Column("source_started_at_utc", DT),
        sa.Column("source_duration_ms", sa.BigInteger()),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("related_event_id", UUID),
        sa.Column("metadata_json", mysql.JSON()),
        sa.Column("deleted_at_utc", DT),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("external_key", name="uq_media_events_external_key"),
        sa.ForeignKeyConstraint(["related_event_id"], ["media_events.id"], ondelete="SET NULL"),
        mysql_charset="utf8mb4",
    )
    for name, cols in [
        ("ix_media_events_platform", ["platform"]),
        ("ix_media_events_media_type", ["media_type"]),
        ("ix_media_events_external_key", ["external_key"]),
        ("ix_media_events_channel_external_id", ["channel_external_id"]),
        ("ix_media_events_channel_login", ["channel_login"]),
        ("ix_media_events_stream_external_id", ["stream_external_id"]),
        ("ix_media_events_video_external_id", ["video_external_id"]),
        ("ix_media_events_related_event_id", ["related_event_id"]),
        ("ix_media_events_deleted_at_utc", ["deleted_at_utc"]),
    ]:
        op.create_index(name, "media_events", cols)

    op.add_column("sessions", sa.Column("event_id", UUID, nullable=True))
    op.create_index("ix_sessions_event_id", "sessions", ["event_id"])

    bind = op.get_bind()
    sessions = sa.table(
        "sessions",
        sa.column("id", UUID),
        sa.column("platform", sa.String(32)),
        sa.column("media_type", sa.String(16)),
        sa.column("channel_external_id", sa.String(64)),
        sa.column("channel_login", sa.String(255)),
        sa.column("channel_display_name", sa.String(255)),
        sa.column("stream_external_id", sa.String(64)),
        sa.column("video_external_id", sa.String(64)),
        sa.column("title", sa.String(1024)),
        sa.column("category_id", sa.String(64)),
        sa.column("category_name", sa.String(255)),
        sa.column("source_started_at_utc", DT),
        sa.column("source_duration_ms", sa.BigInteger()),
        sa.column("metadata_json", mysql.JSON()),
        sa.column("created_at", DT),
        sa.column("updated_at", DT),
        sa.column("event_id", UUID),
    )
    media_events = sa.table(
        "media_events",
        sa.column("id", UUID),
        sa.column("platform", sa.String(32)),
        sa.column("media_type", sa.String(16)),
        sa.column("external_key", sa.String(255)),
        sa.column("channel_external_id", sa.String(64)),
        sa.column("channel_login", sa.String(255)),
        sa.column("channel_display_name", sa.String(255)),
        sa.column("stream_external_id", sa.String(64)),
        sa.column("video_external_id", sa.String(64)),
        sa.column("title", sa.String(1024)),
        sa.column("category_id", sa.String(64)),
        sa.column("category_name", sa.String(255)),
        sa.column("source_started_at_utc", DT),
        sa.column("source_duration_ms", sa.BigInteger()),
        sa.column("source_url", sa.String(2048)),
        sa.column("metadata_json", mysql.JSON()),
        sa.column("created_at", DT),
        sa.column("updated_at", DT),
    )

    existing_rows = [dict(row) for row in bind.execute(sa.select(sessions)).mappings().all()]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in existing_rows:
        grouped[_external_key(row)].append(row)

    for external_key, rows in grouped.items():
        rows.sort(key=lambda row: (row.get("created_at") is not None, row.get("created_at")))
        media_type = _first_non_null(rows, "media_type") or "vod"
        source_started_values = [row["source_started_at_utc"] for row in rows if row.get("source_started_at_utc")]
        source_duration_values = [int(row["source_duration_ms"]) for row in rows if row.get("source_duration_ms") is not None]
        created_values = [row["created_at"] for row in rows if row.get("created_at")]
        updated_values = [row["updated_at"] for row in rows if row.get("updated_at")]
        event_id = uuid.uuid4().bytes
        bind.execute(
            media_events.insert().values(
                id=event_id,
                platform=_first_non_null(rows, "platform") or "twitch",
                media_type=media_type,
                external_key=external_key,
                channel_external_id=_first_non_null(rows, "channel_external_id"),
                channel_login=_first_non_null(rows, "channel_login"),
                channel_display_name=_first_non_null(rows, "channel_display_name"),
                stream_external_id=_first_non_null(rows, "stream_external_id"),
                video_external_id=_first_non_null(rows, "video_external_id"),
                title=_first_non_null(rows, "title"),
                category_id=_first_non_null(rows, "category_id"),
                category_name=_first_non_null(rows, "category_name"),
                source_started_at_utc=min(source_started_values) if source_started_values else None,
                source_duration_ms=max(source_duration_values) if source_duration_values else None,
                source_url=_source_url(rows, media_type),
                metadata_json={
                    "identity_state": "canonical" if not external_key.startswith("legacy:") else "legacy_missing_external_id",
                    "migration_revision": revision,
                    "backfilled_chat_sessions": len(rows),
                },
                created_at=min(created_values) if created_values else sa.func.current_timestamp(6),
                updated_at=max(updated_values) if updated_values else sa.func.current_timestamp(6),
            )
        )
        for row in rows:
            bind.execute(sessions.update().where(sessions.c.id == row["id"]).values(event_id=event_id))

    remaining = bind.scalar(sa.select(sa.func.count()).select_from(sessions).where(sessions.c.event_id.is_(None)))
    if int(remaining or 0) != 0:
        raise RuntimeError(f"event backfill incomplete: {remaining} sessions still have NULL event_id")

    op.alter_column("sessions", "event_id", existing_type=UUID, nullable=False)
    op.create_foreign_key(
        "fk_sessions_event_id_media_events",
        "sessions",
        "media_events",
        ["event_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_event_id_media_events", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_event_id", table_name="sessions")
    op.drop_column("sessions", "event_id")
    op.drop_table("media_events")
