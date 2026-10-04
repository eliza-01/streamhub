from __future__ import annotations

import uuid

import httpx
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.settings import get_settings
from streamhub_common.models import (
    AuditLog,
    ChatMessage,
    ContentCategory,
    EventCategory,
    MediaEvent,
    Session,
    SiteEventPublication,
    TelegramVideoPartBinding,
    VideoPart,
    VideoSession,
)

router = APIRouter(prefix="/api/v1/site", tags=["site"])
settings = get_settings()

MAX_EVENT_CATEGORIES = 3


class SitePublishRequest(BaseModel):
    event_id: uuid.UUID
    categories: list[str] = Field(min_length=1, max_length=MAX_EVENT_CATEGORIES)


class SiteCategoriesRequest(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=MAX_EVENT_CATEGORIES)


def _event_title(event: MediaEvent) -> str:
    return event.title or event.channel_display_name or event.channel_login or event.external_key


def _base_event_payload(event: MediaEvent) -> dict:
    return {
        "id": str(event.id),
        "platform": event.platform,
        "media_type": event.media_type,
        "external_key": event.external_key,
        "channel_login": event.channel_login,
        "channel_display_name": event.channel_display_name,
        "title": _event_title(event),
        "source_started_at_utc": event.source_started_at_utc,
        "source_duration_ms": event.source_duration_ms,
        "source_url": event.source_url,
    }


def _clean_category_slugs(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        slug = str(value or "").strip().lower()
        if slug and slug not in cleaned:
            cleaned.append(slug)
    if not cleaned:
        raise HTTPException(400, "at least one category is required")
    if len(cleaned) > MAX_EVENT_CATEGORIES:
        raise HTTPException(400, f"no more than {MAX_EVENT_CATEGORIES} categories are allowed")
    return cleaned


async def _active_categories(db: AsyncSession) -> list[ContentCategory]:
    return (
        await db.execute(
            select(ContentCategory)
            .where(ContentCategory.is_active.is_(True))
            .order_by(ContentCategory.sort_order, ContentCategory.id)
        )
    ).scalars().all()


async def _category_rows(db: AsyncSession, slugs: list[str]) -> list[ContentCategory]:
    rows = (
        await db.execute(
            select(ContentCategory).where(
                ContentCategory.is_active.is_(True),
                ContentCategory.slug.in_(slugs),
            )
        )
    ).scalars().all()
    by_slug = {row.slug: row for row in rows}
    missing = [slug for slug in slugs if slug not in by_slug]
    if missing:
        raise HTTPException(400, f"unknown or inactive categories: {', '.join(missing)}")
    return [by_slug[slug] for slug in slugs]


async def _storage_event_ids(db: AsyncSession) -> set[uuid.UUID]:
    rows = (
        await db.execute(
            select(VideoSession.event_id)
            .join(VideoPart, VideoPart.video_session_id == VideoSession.id)
            .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
            .where(
                VideoSession.deleted_at_utc.is_(None),
                VideoPart.status == "ready",
            )
            .distinct()
        )
    ).scalars().all()
    return set(rows)


async def _event_categories(db: AsyncSession, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}
    rows = (
        await db.execute(
            select(EventCategory.event_id, EventCategory.position, ContentCategory.slug, ContentCategory.label_ru)
            .join(ContentCategory, ContentCategory.id == EventCategory.category_id)
            .where(EventCategory.event_id.in_(event_ids), ContentCategory.is_active.is_(True))
            .order_by(EventCategory.event_id, EventCategory.position)
        )
    ).all()
    grouped: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for event_id, position, slug, label in rows:
        grouped[event_id].append({"slug": slug, "label": label, "position": int(position)})
    return grouped


async def _chat_summaries(db: AsyncSession, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}
    counts = (
        select(ChatMessage.session_id, func.count(ChatMessage.id).label("message_count"))
        .group_by(ChatMessage.session_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Session, func.coalesce(counts.c.message_count, 0).label("message_count"))
            .outerjoin(counts, counts.c.session_id == Session.id)
            .where(
                Session.event_id.in_(event_ids),
                Session.deleted_at_utc.is_(None),
            )
            .order_by(
                Session.event_id,
                func.coalesce(counts.c.message_count, 0).desc(),
                Session.created_at.desc(),
            )
        )
    ).all()
    grouped: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for session, message_count in rows:
        grouped[session.event_id].append(
            {
                "id": str(session.id),
                "status": session.status,
                "completeness_status": session.completeness_status,
                "source_duration_ms": session.source_duration_ms,
                "coverage_end_ms": session.coverage_end_ms,
                "message_count": int(message_count or 0),
                "created_at": session.created_at,
            }
        )
    return grouped


async def _primary_chat_session(db: AsyncSession, event_id: uuid.UUID) -> tuple[Session, int] | None:
    counts = (
        select(ChatMessage.session_id, func.count(ChatMessage.id).label("message_count"))
        .group_by(ChatMessage.session_id)
        .subquery()
    )
    return (
        await db.execute(
            select(Session, func.coalesce(counts.c.message_count, 0).label("message_count"))
            .outerjoin(counts, counts.c.session_id == Session.id)
            .where(Session.event_id == event_id, Session.deleted_at_utc.is_(None))
            .order_by(func.coalesce(counts.c.message_count, 0).desc(), Session.created_at.desc())
            .limit(1)
        )
    ).first()


async def _video_summaries(db: AsyncSession, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}
    rows = (
        await db.execute(
            select(
                VideoSession,
                func.count(VideoPart.id).label("ready_parts"),
                func.count(TelegramVideoPartBinding.part_id).label("linked_parts"),
            )
            .join(VideoPart, VideoPart.video_session_id == VideoSession.id)
            .outerjoin(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
            .where(
                VideoSession.event_id.in_(event_ids),
                VideoSession.deleted_at_utc.is_(None),
                VideoPart.status == "ready",
            )
            .group_by(VideoSession.id)
            .order_by(VideoSession.event_id, VideoSession.created_at.desc())
        )
    ).all()
    grouped: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for session, ready_parts, linked_parts in rows:
        ready = int(ready_parts or 0)
        linked = int(linked_parts or 0)
        grouped[session.event_id].append(
            {
                "id": str(session.id),
                "status": session.status,
                "completeness_status": session.completeness_status,
                "duration_recorded_ms": int(session.duration_recorded_ms or 0),
                "ready_parts": ready,
                "linked_parts": linked,
                "playable": linked > 0,
                "playback_url": f"/api/v1/playback/video-sessions/{session.id}/index.m3u8" if linked > 0 else None,
                "created_at": session.created_at,
            }
        )
    return grouped


async def _site_payloads(
    db: AsyncSession,
    publication_rows: list[tuple[SiteEventPublication, MediaEvent]],
) -> list[dict]:
    event_ids = [event.id for _publication, event in publication_rows]
    categories = await _event_categories(db, event_ids)
    videos = await _video_summaries(db, event_ids)
    chats = await _chat_summaries(db, event_ids)
    payloads: list[dict] = []
    for publication, event in publication_rows:
        sessions = videos.get(event.id, [])
        chat_sessions = chats.get(event.id, [])
        primary = next((item for item in sessions if item["playable"]), sessions[0] if sessions else None)
        primary_chat = next((item for item in chat_sessions if item["message_count"] > 0), chat_sessions[0] if chat_sessions else None)
        payloads.append(
            {
                **_base_event_payload(event),
                "published_at_utc": publication.published_at_utc,
                "categories": categories.get(event.id, []),
                "video_sessions": sessions,
                "chat_sessions": chat_sessions,
                "primary_chat_session_id": primary_chat["id"] if primary_chat else None,
                "chat_message_count": int(primary_chat["message_count"] if primary_chat else 0),
                "has_chat": bool(primary_chat and primary_chat["message_count"] > 0),
                "ready_parts": sum(item["ready_parts"] for item in sessions),
                "linked_parts": sum(item["linked_parts"] for item in sessions),
                "primary_video_session_id": primary["id"] if primary else None,
                "playback_url": primary["playback_url"] if primary else None,
                "playable": bool(primary and primary["playable"]),
            }
        )
    return payloads


async def _published_event_row(db: AsyncSession, event_id: uuid.UUID) -> tuple[SiteEventPublication, MediaEvent] | None:
    return (
        await db.execute(
            select(SiteEventPublication, MediaEvent)
            .join(MediaEvent, MediaEvent.id == SiteEventPublication.event_id)
            .where(SiteEventPublication.event_id == event_id)
        )
    ).first()


async def _replace_categories(db: AsyncSession, event_id: uuid.UUID, slugs: list[str]) -> None:
    slugs = _clean_category_slugs(slugs)
    rows = await _category_rows(db, slugs)
    await db.execute(delete(EventCategory).where(EventCategory.event_id == event_id))
    for position, category in enumerate(rows, start=1):
        db.add(EventCategory(event_id=event_id, category_id=category.id, position=position))


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)) -> dict:
    rows = await _active_categories(db)
    return {
        "items": [
            {"slug": row.slug, "label": row.label_ru, "sort_order": row.sort_order}
            for row in rows
        ]
    }


@router.get("/feed")
async def site_feed(
    category: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=60, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
) -> dict:
    category_clean = (category or "").strip().lower() or None
    if category_clean == "all":
        category_clean = None
    query_clean = (q or "").strip() or None

    stmt = select(SiteEventPublication, MediaEvent).join(MediaEvent, MediaEvent.id == SiteEventPublication.event_id)
    if category_clean:
        stmt = stmt.where(
            select(EventCategory.event_id)
            .join(ContentCategory, ContentCategory.id == EventCategory.category_id)
            .where(
                EventCategory.event_id == MediaEvent.id,
                ContentCategory.slug == category_clean,
                ContentCategory.is_active.is_(True),
            )
            .exists()
        )
    if query_clean:
        needle = f"%{query_clean}%"
        stmt = stmt.where(
            or_(
                MediaEvent.title.like(needle),
                MediaEvent.channel_display_name.like(needle),
                MediaEvent.channel_login.like(needle),
                MediaEvent.external_key.like(needle),
            )
        )
    rows = (
        await db.execute(
            stmt.order_by(SiteEventPublication.published_at_utc.desc(), MediaEvent.created_at.desc()).limit(limit)
        )
    ).all()
    categories = await _active_categories(db)
    items = await _site_payloads(db, list(rows))
    return {
        "categories": [{"slug": row.slug, "label": row.label_ru} for row in categories],
        "latest": items[0] if items else None,
        "items": items,
    }


@router.get("/events/{event_id}")
async def site_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _published_event_row(db, event_id)
    if row is None:
        raise HTTPException(404, "site event not found")
    return (await _site_payloads(db, [row]))[0]


def _chat_fragments(row: ChatMessage) -> list[dict]:
    value = row.fragments_json
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _chat_message_text(row: ChatMessage) -> str:
    # VOD replay rows historically stored the visible body only in fragments_json.
    # Preserve message_text when it is populated, but reconstruct existing rows
    # from their structured fragments so old captures remain fully renderable.
    if row.message_text:
        return str(row.message_text)
    return "".join(str(item.get("text") or "") for item in _chat_fragments(row))


def _chat_raw_message(row: ChatMessage) -> dict:
    raw = row.raw_payload_json
    if not isinstance(raw, dict):
        return {}
    message = raw.get("message")
    if isinstance(message, dict):
        return message
    payload = raw.get("payload")
    if isinstance(payload, dict):
        event = payload.get("event")
        if isinstance(event, dict):
            nested = event.get("message")
            if isinstance(nested, dict):
                return nested
    return {}


def _chat_color(row: ChatMessage) -> str | None:
    if row.color:
        return str(row.color)
    raw_message = _chat_raw_message(row)
    value = raw_message.get("userColor") or raw_message.get("color")
    return str(value) if value else None


def _chat_badges(row: ChatMessage):
    if row.badges_json not in (None, [], {}):
        return row.badges_json
    raw_message = _chat_raw_message(row)
    return raw_message.get("userBadges") or raw_message.get("badges") or []


@router.get("/events/{event_id}/chat/badges")
async def site_event_chat_badges(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    if await db.get(SiteEventPublication, event_id) is None:
        raise HTTPException(404, "site event not found")
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    broadcaster_id = str(event.channel_external_id or "").strip()
    if not broadcaster_id:
        primary = await _primary_chat_session(db, event_id)
        if primary is not None:
            broadcaster_id = str(primary[0].channel_external_id or "").strip()
    if not broadcaster_id:
        return {"event_id": str(event_id), "broadcaster_id": None, "badges": [], "available": False}

    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.twitch_adapter_base_url}/internal/v1/chat/badges/{broadcaster_id}",
                headers=headers,
            )
        if response.status_code >= 400:
            return {
                "event_id": str(event_id),
                "broadcaster_id": broadcaster_id,
                "badges": [],
                "available": False,
            }
        payload = response.json()
        return {
            "event_id": str(event_id),
            "broadcaster_id": broadcaster_id,
            "badges": payload.get("badges") or [],
            "available": True,
        }
    except httpx.HTTPError:
        return {
            "event_id": str(event_id),
            "broadcaster_id": broadcaster_id,
            "badges": [],
            "available": False,
        }


@router.get("/events/{event_id}/chat/messages")
async def site_event_chat_messages(
    event_id: uuid.UUID,
    from_ms: int = Query(default=0, ge=0),
    to_ms: int = Query(default=60_000, ge=0),
    after_ms: int | None = Query(default=None, ge=0),
    after_id: int = Query(default=0, ge=0),
    page_size: int = Query(default=1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await db.get(SiteEventPublication, event_id) is None:
        raise HTTPException(404, "site event not found")

    start = max(0, int(from_ms))
    end = max(start, int(to_ms))
    primary = await _primary_chat_session(db, event_id)
    if primary is None:
        return {
            "event_id": str(event_id),
            "chat_session_id": None,
            "from_ms": start,
            "to_ms": end,
            "messages": [],
            "has_more": False,
            "next_cursor": None,
        }

    session, _message_count = primary
    conditions = [
        ChatMessage.session_id == session.id,
        ChatMessage.is_deleted.is_(False),
        ChatMessage.timeline_offset_ms >= start,
        ChatMessage.timeline_offset_ms <= end,
    ]
    if after_ms is not None:
        cursor_ms = max(0, int(after_ms))
        conditions.append(
            or_(
                ChatMessage.timeline_offset_ms > cursor_ms,
                and_(ChatMessage.timeline_offset_ms == cursor_ms, ChatMessage.id > max(0, int(after_id))),
            )
        )

    rows = (
        await db.execute(
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.timeline_offset_ms, ChatMessage.id)
            .limit(page_size + 1)
        )
    ).scalars().all()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {"time_ms": int(last.timeline_offset_ms), "id": int(last.id)}

    return {
        "event_id": str(event_id),
        "chat_session_id": str(session.id),
        "from_ms": start,
        "to_ms": end,
        "messages": [
            {
                "id": int(row.id),
                "timeline_offset_ms": int(row.timeline_offset_ms),
                "chatter_external_id": row.chatter_external_id,
                "chatter_login": row.chatter_login,
                "chatter_name": row.chatter_name,
                "color": _chat_color(row),
                "badges": _chat_badges(row),
                "message_text": _chat_message_text(row),
                "fragments": _chat_fragments(row),
                "reply": row.reply_json,
                "bits": row.bits,
                "message_type": row.message_type,
                "is_action": bool(row.is_action),
                "source_kind": row.source_kind,
                "provider_message_id": row.provider_message_id,
                "channel_points_reward_id": row.channel_points_reward_id,
            }
            for row in page
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _chat_user_payload(row) -> dict | None:
    if row is None:
        return None
    return {
        "chatter_external_id": row.chatter_external_id,
        "chatter_login": row.chatter_login,
        "chatter_name": row.chatter_name,
        "message_count": int(row.message_count or 0),
    }


@router.get("/events/{event_id}/chat/stats")
async def site_event_chat_stats(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    if await db.get(SiteEventPublication, event_id) is None:
        raise HTTPException(404, "site event not found")

    primary = await _primary_chat_session(db, event_id)
    if primary is None:
        return {
            "event_id": str(event_id),
            "chat_session_id": None,
            "total_messages": 0,
            "unique_chatters": 0,
            "most_active": None,
            "least_active": None,
        }

    session, total_messages = primary
    identity_key = func.coalesce(
        ChatMessage.chatter_external_id,
        func.lower(ChatMessage.chatter_login),
        func.lower(ChatMessage.chatter_name),
        "unknown",
    )
    grouped = (
        select(
            identity_key.label("identity_key"),
            func.max(ChatMessage.chatter_external_id).label("chatter_external_id"),
            func.max(ChatMessage.chatter_login).label("chatter_login"),
            func.max(ChatMessage.chatter_name).label("chatter_name"),
            func.count(ChatMessage.id).label("message_count"),
        )
        .where(
            ChatMessage.session_id == session.id,
            ChatMessage.is_deleted.is_(False),
        )
        .group_by(identity_key)
        .subquery()
    )
    unique_chatters = int((await db.scalar(select(func.count()).select_from(grouped))) or 0)
    most_active = (
        await db.execute(
            select(grouped)
            .order_by(grouped.c.message_count.desc(), grouped.c.identity_key.asc())
            .limit(1)
        )
    ).first()
    least_active = (
        await db.execute(
            select(grouped)
            .order_by(grouped.c.message_count.asc(), grouped.c.identity_key.asc())
            .limit(1)
        )
    ).first()
    return {
        "event_id": str(event_id),
        "chat_session_id": str(session.id),
        "total_messages": int(total_messages or 0),
        "unique_chatters": unique_chatters,
        "most_active": _chat_user_payload(most_active),
        "least_active": _chat_user_payload(least_active),
    }


@router.get("/events/{event_id}/chat/user-summary")
async def site_event_chat_user_summary(
    event_id: uuid.UUID,
    chatter_external_id: str | None = Query(default=None, max_length=64),
    chatter_login: str | None = Query(default=None, max_length=255),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await db.get(SiteEventPublication, event_id) is None:
        raise HTTPException(404, "site event not found")

    external_id = str(chatter_external_id or "").strip()
    login = str(chatter_login or "").strip()
    if not external_id and not login:
        raise HTTPException(400, "chatter_external_id or chatter_login is required")

    primary = await _primary_chat_session(db, event_id)
    if primary is None:
        return {
            "event_id": str(event_id),
            "chat_session_id": None,
            "chatter_external_id": external_id or None,
            "chatter_login": login or None,
            "message_count": 0,
            "first_message_ms": None,
            "last_message_ms": None,
        }

    session, _message_count = primary
    identity = (
        ChatMessage.chatter_external_id == external_id
        if external_id
        else func.lower(ChatMessage.chatter_login) == login.lower()
    )
    count_row = (
        await db.execute(
            select(
                func.count(ChatMessage.id),
                func.min(ChatMessage.timeline_offset_ms),
                func.max(ChatMessage.timeline_offset_ms),
            ).where(
                ChatMessage.session_id == session.id,
                ChatMessage.is_deleted.is_(False),
                identity,
            )
        )
    ).one()
    count, first_ms, last_ms = count_row
    return {
        "event_id": str(event_id),
        "chat_session_id": str(session.id),
        "chatter_external_id": external_id or None,
        "chatter_login": login or None,
        "message_count": int(count or 0),
        "first_message_ms": int(first_ms) if first_ms is not None else None,
        "last_message_ms": int(last_ms) if last_ms is not None else None,
    }


@router.get("/admin/available-events")
async def site_available_events(db: AsyncSession = Depends(get_db)) -> dict:
    storage_ids = await _storage_event_ids(db)
    if not storage_ids:
        return {"items": []}
    published_ids = set((await db.execute(select(SiteEventPublication.event_id))).scalars().all())
    available_ids = list(storage_ids - published_ids)
    if not available_ids:
        return {"items": []}
    events = (
        await db.execute(
            select(MediaEvent)
            .where(MediaEvent.id.in_(available_ids))
            .order_by(MediaEvent.source_started_at_utc.desc(), MediaEvent.created_at.desc())
        )
    ).scalars().all()
    videos = await _video_summaries(db, [event.id for event in events])
    items = []
    for event in events:
        sessions = videos.get(event.id, [])
        items.append(
            {
                **_base_event_payload(event),
                "video_sessions": len(sessions),
                "ready_parts": sum(item["ready_parts"] for item in sessions),
                "linked_parts": sum(item["linked_parts"] for item in sessions),
            }
        )
    return {"items": items}


@router.post("/admin/events", status_code=status.HTTP_201_CREATED)
async def publish_site_event(payload: SitePublishRequest, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, payload.event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    if await db.get(SiteEventPublication, payload.event_id) is not None:
        raise HTTPException(409, "event is already published on the site")
    if payload.event_id not in await _storage_event_ids(db):
        raise HTTPException(409, "event has no Telegram-linked ready video parts")

    publication = SiteEventPublication(
        event_id=event.id,
        published_at_utc=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(publication)
    await db.flush()
    category_slugs = _clean_category_slugs(payload.categories)
    await _replace_categories(db, event.id, category_slugs)
    db.add(AuditLog(event_id=event.id, action="site_publish", payload_json={"categories": category_slugs}))
    await db.commit()

    row = await _published_event_row(db, event.id)
    if row is None:
        raise HTTPException(500, "published event disappeared")
    return (await _site_payloads(db, [row]))[0]


@router.put("/admin/events/{event_id}/categories")
async def update_site_event_categories(
    event_id: uuid.UUID,
    payload: SiteCategoriesRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await db.get(SiteEventPublication, event_id) is None:
        raise HTTPException(404, "site event not found")
    category_slugs = _clean_category_slugs(payload.categories)
    await _replace_categories(db, event_id, category_slugs)
    db.add(AuditLog(event_id=event_id, action="site_categories_update", payload_json={"categories": category_slugs}))
    await db.commit()
    row = await _published_event_row(db, event_id)
    if row is None:
        raise HTTPException(404, "site event not found")
    return (await _site_payloads(db, [row]))[0]


@router.delete("/admin/events/{event_id}")
async def unpublish_site_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    publication = await db.get(SiteEventPublication, event_id)
    if publication is None:
        return {"ok": True, "already_unpublished": True}
    await db.execute(delete(EventCategory).where(EventCategory.event_id == event_id))
    await db.delete(publication)
    db.add(AuditLog(event_id=event_id, action="site_unpublish", payload_json={}))
    await db.commit()
    return {"ok": True, "event_id": str(event_id)}
