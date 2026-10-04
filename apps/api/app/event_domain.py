from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.event_identity import build_event_identity, canonical_source_url
from streamhub_common.models import MediaEvent


async def resolve_or_create_media_event(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    platform: str,
    media_type: str,
    channel_external_id: str | None,
    channel_login: str | None,
    channel_display_name: str | None,
    stream_external_id: str | None,
    video_external_id: str | None,
    title: str | None,
    category_id: str | None,
    category_name: str | None,
    source_started_at_utc: datetime | None,
    source_duration_ms: int | None,
    page_url: str | None,
) -> MediaEvent:
    identity = build_event_identity(
        platform=platform,
        media_type=media_type,
        session_id=session_id,
        video_external_id=video_external_id,
        stream_external_id=stream_external_id,
    )

    event = await db.scalar(select(MediaEvent).where(MediaEvent.external_key == identity.external_key))
    if event is None:
        event = MediaEvent(
            id=uuid.uuid4(),
            platform=platform or "twitch",
            media_type=media_type,
            external_key=identity.external_key,
            channel_external_id=channel_external_id,
            channel_login=channel_login,
            channel_display_name=channel_display_name,
            stream_external_id=stream_external_id,
            video_external_id=video_external_id,
            title=title,
            display_title=title,
            category_id=category_id,
            category_name=category_name,
            source_started_at_utc=source_started_at_utc,
            source_duration_ms=source_duration_ms,
            source_url=canonical_source_url(
                media_type=media_type,
                video_external_id=video_external_id,
                channel_login=channel_login,
                page_url=page_url,
            ),
            metadata_json={"identity_state": identity.identity_state},
        )
        try:
            async with db.begin_nested():
                db.add(event)
                await db.flush()
        except IntegrityError:
            # A concurrent Start may have inserted the same canonical event.
            event = await db.scalar(select(MediaEvent).where(MediaEvent.external_key == identity.external_key))
            if event is None:  # pragma: no cover - defensive database invariant
                raise

    # Event metadata is a snapshot/read model. Fill missing values and accept
    # newer concrete metadata without changing chat capture semantics.
    event.channel_external_id = channel_external_id or event.channel_external_id
    event.channel_login = channel_login or event.channel_login
    event.channel_display_name = channel_display_name or event.channel_display_name
    event.stream_external_id = stream_external_id or event.stream_external_id
    event.video_external_id = video_external_id or event.video_external_id
    event.title = title or event.title
    if not event.display_title and event.title:
        event.display_title = event.title
    event.category_id = category_id or event.category_id
    event.category_name = category_name or event.category_name
    event.source_started_at_utc = source_started_at_utc or event.source_started_at_utc
    if source_duration_ms is not None:
        event.source_duration_ms = max(int(source_duration_ms), int(event.source_duration_ms or 0))
    event.source_url = canonical_source_url(
        media_type=media_type,
        video_external_id=video_external_id or event.video_external_id,
        channel_login=channel_login or event.channel_login,
        page_url=page_url,
    ) or event.source_url
    metadata = dict(event.metadata_json or {})
    if identity.identity_state == "canonical":
        metadata["identity_state"] = "canonical"
    event.metadata_json = metadata
    return event
