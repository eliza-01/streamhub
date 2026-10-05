from __future__ import annotations

import re
import uuid
from typing import Literal

import httpx
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
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
    SiteEventAsset,
    SiteEventPublication,
    SiteComment,
    SiteCommentReaction,
    SiteEventTimecode,
    SiteWatchMessage,
    SiteWatchParticipant,
    SiteWatchSession,
    TelegramVideoPartBinding,
    VideoPart,
    VideoPartSegment,
    VideoRun,
    VideoSegment,
    VideoSession,
    User,
)

from .user_auth import optional_current_user, require_current_user

from ..site_assets import (
    SITE_ASSET_SLOTS,
    prepare_and_store_site_asset,
    remove_site_asset_file,
    site_asset_path,
    validate_site_asset_slot,
)

router = APIRouter(prefix="/api/v1/site", tags=["site"])
settings = get_settings()

MAX_EVENT_CATEGORIES = 3
_COMMENT_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SitePublishRequest(BaseModel):
    event_id: uuid.UUID
    categories: list[str] = Field(min_length=1, max_length=MAX_EVENT_CATEGORIES)


class SiteCategoriesRequest(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=MAX_EVENT_CATEGORIES)


class SiteEventAdminUpdateRequest(BaseModel):
    display_title: str = Field(min_length=1, max_length=1024)


class SiteVisibilityRequest(BaseModel):
    hidden: bool


class SiteTimecodeInput(BaseModel):
    offset_ms: int = Field(ge=0, le=2_592_000_000)
    title: str = Field(min_length=1, max_length=255)


class SiteTimecodesRequest(BaseModel):
    items: list[SiteTimecodeInput] = Field(default_factory=list, max_length=200)


class SiteCommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    text_color: str = Field(default="#ff9b37", min_length=7, max_length=7)
    is_underlined: bool = False
    parent_comment_id: int | None = Field(default=None, ge=1)


SiteCommentReactionName = Literal["like", "dislike", "heart", "broken_heart", "fire", "cry", "laugh", "poop"]


class SiteCommentReactionRequest(BaseModel):
    reaction: SiteCommentReactionName


class SiteWatchClientRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")


class SiteWatchCreateRequest(SiteWatchClientRequest):
    position_ms: int = Field(default=0, ge=0, le=2_592_000_000)
    is_playing: bool = False


class SiteWatchStateRequest(SiteWatchClientRequest):
    position_ms: int = Field(ge=0, le=2_592_000_000)
    is_playing: bool


class SiteWatchMessageCreateRequest(SiteWatchClientRequest):
    body: str = Field(min_length=1, max_length=1000)


def _event_title(event: MediaEvent) -> str:
    return event.display_title or event.title or event.channel_display_name or event.channel_login or event.external_key


def _base_event_payload(event: MediaEvent) -> dict:
    return {
        "id": str(event.id),
        "platform": event.platform,
        "media_type": event.media_type,
        "external_key": event.external_key,
        "channel_login": event.channel_login,
        "channel_display_name": event.channel_display_name,
        "title": _event_title(event),
        "display_title": _event_title(event),
        "source_title": event.title,
        "source_started_at_utc": event.source_started_at_utc,
        "source_duration_ms": event.source_duration_ms,
        "source_url": event.source_url,
    }


def _site_asset_payload(asset: SiteEventAsset) -> dict:
    return {
        "slot": asset.slot,
        "url": f"/api/v1/site/assets/{asset.event_id}/{asset.slot}/{asset.sha256}.webp",
        "content_type": asset.content_type,
        "size_bytes": int(asset.size_bytes),
        "width": int(asset.width),
        "height": int(asset.height),
        "sha256": asset.sha256,
        "original_filename": asset.original_filename,
        "updated_at": asset.updated_at,
    }


async def _event_assets(
    db: AsyncSession, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, SiteEventAsset]]:
    if not event_ids:
        return {}
    rows = (
        await db.execute(
            select(SiteEventAsset)
            .where(SiteEventAsset.event_id.in_(event_ids))
            .order_by(SiteEventAsset.event_id, SiteEventAsset.slot)
        )
    ).scalars().all()
    grouped: dict[uuid.UUID, dict[str, SiteEventAsset]] = defaultdict(dict)
    for row in rows:
        grouped[row.event_id][row.slot] = row
    return grouped


def _site_assets_payload(rows: dict[str, SiteEventAsset]) -> dict:
    cover = rows.get("cover")
    frames = [rows.get(f"frame_{index}") for index in range(1, 5)]
    return {
        "cover": _site_asset_payload(cover) if cover else None,
        "frames": [_site_asset_payload(frame) if frame else None for frame in frames],
        "complete": all(rows.get(slot) is not None for slot in SITE_ASSET_SLOTS),
    }


def _site_timecode_payload(row: SiteEventTimecode) -> dict:
    return {
        "id": int(row.id),
        "position": int(row.position),
        "offset_ms": int(row.offset_ms),
        "title": row.title,
    }


async def _event_timecodes(
    db: AsyncSession, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SiteEventTimecode]]:
    if not event_ids:
        return {}
    rows = (
        await db.execute(
            select(SiteEventTimecode)
            .where(SiteEventTimecode.event_id.in_(event_ids))
            .order_by(SiteEventTimecode.event_id, SiteEventTimecode.position, SiteEventTimecode.offset_ms)
        )
    ).scalars().all()
    grouped: dict[uuid.UUID, list[SiteEventTimecode]] = defaultdict(list)
    for row in rows:
        grouped[row.event_id].append(row)
    return grouped


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


async def _event_has_public_storage(db: AsyncSession, event_id: uuid.UUID) -> bool:
    row = (
        await db.execute(
            select(VideoSession.id)
            .join(VideoPart, VideoPart.video_session_id == VideoSession.id)
            .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
            .where(
                VideoSession.event_id == event_id,
                VideoSession.deleted_at_utc.is_(None),
                VideoPart.status == "ready",
            )
            .limit(1)
        )
    ).first()
    return row is not None


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
    *,
    include_timecodes: bool = False,
) -> list[dict]:
    event_ids = [event.id for _publication, event in publication_rows]
    categories = await _event_categories(db, event_ids)
    videos = await _video_summaries(db, event_ids)
    chats = await _chat_summaries(db, event_ids)
    assets = await _event_assets(db, event_ids)
    timecodes = await _event_timecodes(db, event_ids) if include_timecodes else {}
    payloads: list[dict] = []
    for publication, event in publication_rows:
        sessions = videos.get(event.id, [])
        chat_sessions = chats.get(event.id, [])
        primary = next((item for item in sessions if item["playable"]), sessions[0] if sessions else None)
        primary_chat = next((item for item in chat_sessions if item["message_count"] > 0), chat_sessions[0] if chat_sessions else None)
        payload = {
            **_base_event_payload(event),
            "published_at_utc": publication.published_at_utc,
            "categories": categories.get(event.id, []),
            "assets": _site_assets_payload(assets.get(event.id, {})),
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
        if include_timecodes:
            payload["timecodes"] = [_site_timecode_payload(row) for row in timecodes.get(event.id, [])]
        payloads.append(payload)
    return payloads


async def _published_event_row(db: AsyncSession, event_id: uuid.UUID) -> tuple[SiteEventPublication, MediaEvent] | None:
    return (
        await db.execute(
            select(SiteEventPublication, MediaEvent)
            .join(MediaEvent, MediaEvent.id == SiteEventPublication.event_id)
            .where(SiteEventPublication.event_id == event_id)
        )
    ).first()


async def _public_event_row(db: AsyncSession, event_id: uuid.UUID) -> tuple[SiteEventPublication, MediaEvent] | None:
    row = await _published_event_row(db, event_id)
    if row is None or row[0].hidden_at_utc is not None:
        return None
    if not await _event_has_public_storage(db, event_id):
        return None
    return row


async def _require_public_event(db: AsyncSession, event_id: uuid.UUID) -> tuple[SiteEventPublication, MediaEvent]:
    row = await _public_event_row(db, event_id)
    if row is None:
        raise HTTPException(404, "site event not found")
    return row


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


@router.get("/assets/{event_id}/{slot}/{sha256}.webp")
async def site_asset(
    event_id: uuid.UUID,
    slot: str,
    sha256: str,
    db: AsyncSession = Depends(get_db),
):
    validate_site_asset_slot(slot)
    asset = await db.get(SiteEventAsset, (event_id, slot))
    if asset is None or asset.sha256 != sha256:
        raise HTTPException(404, "site asset not found")
    path = site_asset_path(settings, asset.storage_key)
    if not path.is_file():
        raise HTTPException(404, "site asset file not found")
    return FileResponse(
        path,
        media_type=asset.content_type,
        filename=None,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{asset.sha256}"',
        },
    )


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

    storage_ids = await _storage_event_ids(db)
    if not storage_ids:
        categories = await _active_categories(db)
        return {
            "categories": [{"slug": row.slug, "label": row.label_ru} for row in categories],
            "latest": None,
            "items": [],
        }

    stmt = (
        select(SiteEventPublication, MediaEvent)
        .join(MediaEvent, MediaEvent.id == SiteEventPublication.event_id)
        .where(
            SiteEventPublication.hidden_at_utc.is_(None),
            MediaEvent.id.in_(storage_ids),
        )
    )
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
                MediaEvent.display_title.like(needle),
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
    row = await _require_public_event(db, event_id)
    return (await _site_payloads(db, [row], include_timecodes=True))[0]


def _site_comment_payload(
    comment: SiteComment,
    user: User,
    *,
    reactions: dict[str, int] | None = None,
    viewer_reaction: str | None = None,
) -> dict:
    avatar_url = None
    if user.avatar_path:
        version = int(user.updated_at.timestamp()) if user.updated_at else 0
        avatar_url = f"/api/v1/user-auth/users/{user.id}/avatar?v={version}"
    return {
        "id": int(comment.id),
        "event_id": str(comment.event_id),
        "parent_comment_id": int(comment.parent_comment_id) if comment.parent_comment_id is not None else None,
        "body": comment.body,
        "text_color": comment.text_color or "#ff9b37",
        "is_underlined": bool(comment.is_underlined),
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "reactions": reactions or {},
        "viewer_reaction": viewer_reaction,
        "user": {
            "id": str(user.id),
            "nickname": user.nickname,
            "login": user.login,
            "avatar_url": avatar_url,
        },
    }


async def _comment_reaction_state(
    db: AsyncSession,
    comment_ids: list[int],
    viewer_user_id: uuid.UUID | None,
) -> tuple[dict[int, dict[str, int]], dict[int, str]]:
    if not comment_ids:
        return {}, {}
    count_rows = (
        await db.execute(
            select(SiteCommentReaction.comment_id, SiteCommentReaction.reaction, func.count())
            .where(SiteCommentReaction.comment_id.in_(comment_ids))
            .group_by(SiteCommentReaction.comment_id, SiteCommentReaction.reaction)
        )
    ).all()
    counts: dict[int, dict[str, int]] = defaultdict(dict)
    for comment_id, reaction, count in count_rows:
        counts[int(comment_id)][str(reaction)] = int(count)

    viewer: dict[int, str] = {}
    if viewer_user_id is not None:
        viewer_rows = (
            await db.execute(
                select(SiteCommentReaction.comment_id, SiteCommentReaction.reaction).where(
                    SiteCommentReaction.comment_id.in_(comment_ids),
                    SiteCommentReaction.user_id == viewer_user_id,
                )
            )
        ).all()
        viewer = {int(comment_id): str(reaction) for comment_id, reaction in viewer_rows}
    return counts, viewer


@router.get("/events/{event_id}/comments")
async def site_event_comments(
    event_id: uuid.UUID,
    page_size: int = Query(default=100, ge=1, le=200),
    viewer: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_public_event(db, event_id)
    rows = (
        await db.execute(
            select(SiteComment, User)
            .join(User, User.id == SiteComment.user_id)
            .where(SiteComment.event_id == event_id, User.is_active.is_(True))
            .order_by(SiteComment.created_at.desc(), SiteComment.id.desc())
            .limit(page_size)
        )
    ).all()
    comment_ids = [int(comment.id) for comment, _user in rows]
    counts, viewer_reactions = await _comment_reaction_state(db, comment_ids, viewer.id if viewer else None)
    return {
        "event_id": str(event_id),
        "items": [
            _site_comment_payload(
                comment,
                user,
                reactions=counts.get(int(comment.id), {}),
                viewer_reaction=viewer_reactions.get(int(comment.id)),
            )
            for comment, user in rows
        ],
    }


@router.post("/events/{event_id}/comments", status_code=201)
async def create_site_event_comment(
    event_id: uuid.UUID,
    payload: SiteCommentCreateRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_public_event(db, event_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(400, "Комментарий не может быть пустым")
    text_color = payload.text_color.lower()
    if not _COMMENT_COLOR_RE.fullmatch(text_color):
        raise HTTPException(400, "Некорректный цвет комментария")

    parent_comment_id = payload.parent_comment_id
    if parent_comment_id is not None:
        parent = await db.get(SiteComment, parent_comment_id)
        if not parent or parent.event_id != event_id:
            raise HTTPException(400, "Комментарий для ответа не найден")

    now = datetime.utcnow()
    # Serialize comment creation per user so concurrent clicks/tabs cannot bypass the 60s limit.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    latest = (
        await db.execute(
            select(SiteComment)
            .where(SiteComment.user_id == user.id)
            .order_by(SiteComment.created_at.desc(), SiteComment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest and latest.created_at:
        elapsed = max(0.0, (now - latest.created_at).total_seconds())
        if elapsed < 60:
            retry_after = max(1, int(60 - elapsed + 0.999))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "message": "Можно отправлять не больше одного комментария в минуту",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

    comment = SiteComment(
        event_id=event_id,
        user_id=user.id,
        parent_comment_id=parent_comment_id,
        body=body,
        text_color=text_color,
        is_underlined=bool(payload.is_underlined),
        created_at=now,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {
        "comment": _site_comment_payload(comment, user),
        "cooldown_seconds": 60,
        "next_allowed_at": (now + timedelta(seconds=60)).isoformat(),
    }


@router.post("/comments/{comment_id}/reaction")
async def toggle_site_comment_reaction(
    comment_id: int,
    payload: SiteCommentReactionRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    comment = await db.get(SiteComment, comment_id)
    if not comment:
        raise HTTPException(404, "Комментарий не найден")
    await _require_public_event(db, comment.event_id)

    existing = await db.get(SiteCommentReaction, {"comment_id": comment_id, "user_id": user.id})
    viewer_reaction: str | None
    if existing and existing.reaction == payload.reaction:
        await db.delete(existing)
        viewer_reaction = None
    elif existing:
        existing.reaction = payload.reaction
        existing.updated_at = datetime.utcnow()
        viewer_reaction = payload.reaction
    else:
        db.add(
            SiteCommentReaction(
                comment_id=comment_id,
                user_id=user.id,
                reaction=payload.reaction,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        viewer_reaction = payload.reaction
    await db.commit()
    counts, _viewer = await _comment_reaction_state(db, [comment_id], user.id)
    return {
        "comment_id": comment_id,
        "reactions": counts.get(comment_id, {}),
        "viewer_reaction": viewer_reaction,
    }


_WATCH_SESSION_TTL = timedelta(seconds=60)
_WATCH_PARTICIPANT_TTL = timedelta(seconds=15)


def _watch_display_name(user: User | None, client_id: str) -> str:
    if user is not None:
        return user.nickname[:64]
    compact = re.sub(r"[^A-Za-z0-9]", "", client_id)[-4:] or "guest"
    return f"Гость {compact.upper()}"


async def _touch_watch_participant(
    db: AsyncSession,
    session: SiteWatchSession,
    *,
    client_id: str,
    user: User | None,
) -> SiteWatchParticipant:
    participant = (
        await db.execute(
            select(SiteWatchParticipant).where(
                SiteWatchParticipant.session_id == session.id,
                SiteWatchParticipant.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    now = datetime.utcnow()
    if participant is None:
        participant = SiteWatchParticipant(
            session_id=session.id,
            user_id=user.id if user else None,
            client_id=client_id,
            display_name=_watch_display_name(user, client_id),
            joined_at=now,
            last_seen_at=now,
        )
        db.add(participant)
    else:
        participant.user_id = user.id if user else participant.user_id
        participant.display_name = _watch_display_name(user, client_id)
        participant.last_seen_at = now
    session.last_activity_at = now
    return participant


async def _watch_participant_count(db: AsyncSession, session_id: uuid.UUID, *, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    cutoff = now - _WATCH_PARTICIPANT_TTL
    return int(
        (
            await db.execute(
                select(func.count(SiteWatchParticipant.id)).where(
                    SiteWatchParticipant.session_id == session_id,
                    SiteWatchParticipant.last_seen_at >= cutoff,
                )
            )
        ).scalar_one()
        or 0
    )


async def _watch_session_payload(db: AsyncSession, session: SiteWatchSession, *, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    return {
        "id": str(session.id),
        "event_id": str(session.event_id),
        "owner_user_id": str(session.owner_user_id) if session.owner_user_id else None,
        "owner_label": session.owner_label,
        "participant_count": await _watch_participant_count(db, session.id, now=now),
        "position_ms": int(session.position_ms or 0),
        "is_playing": bool(session.is_playing),
        "state_version": int(session.state_version or 0),
        "state_updated_at": session.state_updated_at.isoformat() if session.state_updated_at else None,
        "last_activity_at": session.last_activity_at.isoformat() if session.last_activity_at else None,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }


def _watch_message_user_payload(user: User) -> dict:
    avatar_url = None
    if user.avatar_path:
        version = int(user.updated_at.timestamp()) if user.updated_at else 0
        avatar_url = f"/api/v1/user-auth/users/{user.id}/avatar?v={version}"
    return {
        "id": str(user.id),
        "nickname": user.nickname,
        "login": user.login,
        "avatar_url": avatar_url,
    }


def _watch_message_payload(message: SiteWatchMessage, user: User) -> dict:
    return {
        "id": int(message.id),
        "session_id": str(message.session_id),
        "body": message.body,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "user": _watch_message_user_payload(user),
    }


async def _require_active_watch_session(db: AsyncSession, session_id: uuid.UUID) -> SiteWatchSession:
    session = await db.get(SiteWatchSession, session_id)
    now = datetime.utcnow()
    if (
        session is None
        or session.closed_at is not None
        or session.last_activity_at < now - _WATCH_SESSION_TTL
    ):
        raise HTTPException(404, "Сессия совместного просмотра недоступна")
    await _require_public_event(db, session.event_id)
    return session


@router.get("/events/{event_id}/watch-sessions")
async def site_watch_sessions(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    await _require_public_event(db, event_id)
    now = datetime.utcnow()
    rows = (
        await db.execute(
            select(SiteWatchSession)
            .where(
                SiteWatchSession.event_id == event_id,
                SiteWatchSession.closed_at.is_(None),
                SiteWatchSession.last_activity_at >= now - _WATCH_SESSION_TTL,
            )
            .order_by(SiteWatchSession.last_activity_at.desc(), SiteWatchSession.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return {
        "event_id": str(event_id),
        "server_now_utc": now.isoformat(),
        "items": [await _watch_session_payload(db, row, now=now) for row in rows],
    }


@router.post("/events/{event_id}/watch-sessions", status_code=201)
async def create_site_watch_session(
    event_id: uuid.UUID,
    payload: SiteWatchCreateRequest,
    viewer: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_public_event(db, event_id)
    now = datetime.utcnow()
    session = SiteWatchSession(
        event_id=event_id,
        owner_user_id=viewer.id if viewer else None,
        owner_client_id=payload.client_id,
        owner_label=_watch_display_name(viewer, payload.client_id),
        position_ms=payload.position_ms,
        is_playing=payload.is_playing,
        state_version=1,
        state_updated_at=now,
        last_activity_at=now,
        created_at=now,
    )
    db.add(session)
    await db.flush()
    await _touch_watch_participant(db, session, client_id=payload.client_id, user=viewer)
    await db.commit()
    await db.refresh(session)
    return {
        "server_now_utc": now.isoformat(),
        "session": await _watch_session_payload(db, session, now=now),
    }


@router.post("/watch-sessions/{session_id}/join")
async def join_site_watch_session(
    session_id: uuid.UUID,
    payload: SiteWatchClientRequest,
    viewer: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _require_active_watch_session(db, session_id)
    now = datetime.utcnow()
    await _touch_watch_participant(db, session, client_id=payload.client_id, user=viewer)
    await db.commit()
    await db.refresh(session)
    return {
        "server_now_utc": now.isoformat(),
        "session": await _watch_session_payload(db, session, now=now),
    }


@router.post("/watch-sessions/{session_id}/heartbeat")
async def heartbeat_site_watch_session(
    session_id: uuid.UUID,
    payload: SiteWatchClientRequest,
    viewer: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _require_active_watch_session(db, session_id)
    now = datetime.utcnow()
    await _touch_watch_participant(db, session, client_id=payload.client_id, user=viewer)
    await db.commit()
    await db.refresh(session)
    return {
        "server_now_utc": now.isoformat(),
        "session": await _watch_session_payload(db, session, now=now),
    }


@router.put("/watch-sessions/{session_id}/state")
async def update_site_watch_session_state(
    session_id: uuid.UUID,
    payload: SiteWatchStateRequest,
    viewer: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _require_active_watch_session(db, session_id)
    participant = (
        await db.execute(
            select(SiteWatchParticipant).where(
                SiteWatchParticipant.session_id == session.id,
                SiteWatchParticipant.client_id == payload.client_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(409, "Сначала присоединитесь к сессии")
    now = datetime.utcnow()
    participant.last_seen_at = now
    if viewer is not None:
        participant.user_id = viewer.id
        participant.display_name = viewer.nickname[:64]
    session.position_ms = payload.position_ms
    session.is_playing = payload.is_playing
    session.state_version = int(session.state_version or 0) + 1
    session.state_updated_at = now
    session.last_activity_at = now
    await db.commit()
    await db.refresh(session)
    return {
        "server_now_utc": now.isoformat(),
        "session": await _watch_session_payload(db, session, now=now),
    }


@router.get("/watch-sessions/{session_id}/messages")
async def site_watch_session_messages(
    session_id: uuid.UUID,
    after_id: int | None = Query(default=None, ge=0),
    page_size: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _require_active_watch_session(db, session_id)
    query = (
        select(SiteWatchMessage, User)
        .join(User, User.id == SiteWatchMessage.user_id)
        .where(SiteWatchMessage.session_id == session.id)
    )
    if after_id is not None:
        query = query.where(SiteWatchMessage.id > after_id)
    rows = (
        await db.execute(query.order_by(SiteWatchMessage.id.asc()).limit(page_size))
    ).all()
    return {
        "session_id": str(session.id),
        "items": [_watch_message_payload(message, user) for message, user in rows],
    }


@router.post("/watch-sessions/{session_id}/messages", status_code=201)
async def create_site_watch_session_message(
    session_id: uuid.UUID,
    payload: SiteWatchMessageCreateRequest,
    viewer: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _require_active_watch_session(db, session_id)
    participant = (
        await db.execute(
            select(SiteWatchParticipant).where(
                SiteWatchParticipant.session_id == session.id,
                SiteWatchParticipant.client_id == payload.client_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(409, "Сначала присоединитесь к комнате")
    body = payload.body.strip()
    if not body:
        raise HTTPException(422, "Сообщение пустое")
    now = datetime.utcnow()
    participant.last_seen_at = now
    participant.user_id = viewer.id
    participant.display_name = viewer.nickname[:64]
    session.last_activity_at = now
    message = SiteWatchMessage(session_id=session.id, user_id=viewer.id, body=body, created_at=now)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return {"message": _watch_message_payload(message, viewer)}


@router.post("/watch-sessions/{session_id}/leave")
async def leave_site_watch_session(
    session_id: uuid.UUID,
    payload: SiteWatchClientRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await db.get(SiteWatchSession, session_id)
    if session is None:
        return {"ok": True}
    participant = (
        await db.execute(
            select(SiteWatchParticipant).where(
                SiteWatchParticipant.session_id == session.id,
                SiteWatchParticipant.client_id == payload.client_id,
            )
        )
    ).scalar_one_or_none()
    if participant is not None:
        await db.delete(participant)
        await db.flush()
    remaining = await _watch_participant_count(db, session.id)
    if remaining == 0:
        session.closed_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "closed": remaining == 0}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _clock_offset_ms(value: datetime | None, origin: datetime | None) -> int | None:
    value_naive = _naive_utc(value)
    origin_naive = _naive_utc(origin)
    if value_naive is None or origin_naive is None:
        return None
    return max(0, int(round((value_naive - origin_naive).total_seconds() * 1000)))


def _merge_timeline_range(
    ranges: list[dict],
    *,
    run_no: int,
    segment_no: int,
    timeline_start_ms: int,
    timeline_end_ms: int,
    source_start_ms: int,
    source_end_ms: int,
    mapping_source: str,
) -> None:
    previous = ranges[-1] if ranges else None
    contiguous = bool(
        previous
        and previous["run_no"] == run_no
        and previous["mapping_source"] == mapping_source
        and abs(timeline_start_ms - previous["timeline_end_ms"]) <= 2500
        and abs(source_start_ms - previous["source_end_ms"]) <= 2500
    )
    if contiguous:
        previous["timeline_end_ms"] = timeline_end_ms
        previous["source_end_ms"] = source_end_ms
        previous["last_segment_no"] = segment_no
        return
    ranges.append(
        {
            "run_no": run_no,
            "first_segment_no": segment_no,
            "last_segment_no": segment_no,
            "timeline_start_ms": timeline_start_ms,
            "timeline_end_ms": timeline_end_ms,
            "source_start_ms": source_start_ms,
            "source_end_ms": source_end_ms,
            "mapping_source": mapping_source,
        }
    )


@router.get("/events/{event_id}/playback-timeline")
async def site_event_playback_timeline(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    publication_row = await _require_public_event(db, event_id)
    _publication, event = publication_row

    video_summaries = (await _video_summaries(db, [event_id])).get(event_id, [])
    primary = next((item for item in video_summaries if item["playable"]), None)
    if primary is None:
        return {
            "event_id": str(event_id),
            "video_session_id": None,
            "synchronized": False,
            "mapping_quality": "unavailable",
            "ranges": [],
        }

    session_id = uuid.UUID(primary["id"])
    # Playback is built only from Telegram-linked ready part segments. Its
    # browser currentTime starts at zero for the first linked segment, which is
    # not necessarily the same as VideoSegment.timeline_start_ms when only a
    # slice of a recording has been uploaded. Build the map against the exact
    # same segment set and cumulative durations as the Telegram HLS playlist.
    rows = (
        await db.execute(
            select(VideoSegment, VideoRun)
            .join(VideoRun, VideoRun.id == VideoSegment.video_run_id)
            .join(VideoPartSegment, VideoPartSegment.segment_id == VideoSegment.id)
            .join(VideoPart, VideoPart.id == VideoPartSegment.part_id)
            .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
            .where(
                VideoPart.video_session_id == session_id,
                VideoPart.status == "ready",
            )
            .order_by(VideoSegment.segment_no, VideoPart.part_no)
        )
    ).all()
    if not rows:
        return {
            "event_id": str(event_id),
            "video_session_id": str(session_id),
            "synchronized": False,
            "mapping_quality": "unavailable",
            "ranges": [],
        }

    run_first_rows = (
        await db.execute(
            select(VideoSegment.video_run_id, func.min(VideoSegment.timeline_start_ms))
            .where(VideoSegment.video_session_id == session_id)
            .group_by(VideoSegment.video_run_id)
        )
    ).all()
    first_timeline_by_run = {int(run_id): int(first_ms) for run_id, first_ms in run_first_rows}

    ranges: list[dict] = []
    mapping_sources: set[str] = set()
    seen_segments: set[int] = set()
    player_cursor = 0
    for segment, run in rows:
        if int(segment.id) in seen_segments:
            continue
        seen_segments.add(int(segment.id))
        original_timeline_start = int(segment.timeline_start_ms)
        duration = max(0, int(segment.duration_ms))
        timeline_start = player_cursor
        timeline_end = timeline_start + duration
        player_cursor = timeline_end
        source_start = int(segment.source_media_start_ms) if segment.source_media_start_ms is not None else None
        source_end = int(segment.source_media_end_ms) if segment.source_media_end_ms is not None else None
        mapping_source = "segment_source"

        if source_start is None or source_end is None:
            if event.media_type == "vod":
                source_start = original_timeline_start
                source_end = source_start + duration
                mapping_source = "vod_timeline"
            else:
                run_source_start = _clock_offset_ms(run.started_at_utc, event.source_started_at_utc)
                if run_source_start is None:
                    continue
                run_timeline_start = first_timeline_by_run.get(int(run.id), original_timeline_start)
                source_start = max(0, run_source_start + original_timeline_start - run_timeline_start)
                source_end = source_start + duration
                mapping_source = "run_clock_fallback"

        mapping_sources.add(mapping_source)
        _merge_timeline_range(
            ranges,
            run_no=int(run.run_no),
            segment_no=int(segment.segment_no),
            timeline_start_ms=timeline_start,
            timeline_end_ms=timeline_end,
            source_start_ms=source_start,
            source_end_ms=source_end,
            mapping_source=mapping_source,
        )

    if not ranges:
        quality = "unavailable"
    elif mapping_sources == {"run_clock_fallback"}:
        quality = "approximate"
    elif "run_clock_fallback" in mapping_sources:
        quality = "mixed"
    else:
        quality = "exact"
    return {
        "event_id": str(event_id),
        "video_session_id": str(session_id),
        "synchronized": bool(ranges),
        "mapping_quality": quality,
        "ranges": ranges,
    }


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


def _chat_message_payload(row: ChatMessage) -> dict:
    return {
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


def _chat_identity(
    chatter_external_id: str | None,
    chatter_login: str | None,
):
    external_id = str(chatter_external_id or "").strip()
    login = str(chatter_login or "").strip()
    if not external_id and not login:
        raise HTTPException(400, "chatter_external_id or chatter_login is required")
    if external_id and login:
        condition = or_(
            ChatMessage.chatter_external_id == external_id,
            and_(
                ChatMessage.chatter_external_id.is_(None),
                func.lower(ChatMessage.chatter_login) == login.lower(),
            ),
        )
    elif external_id:
        condition = ChatMessage.chatter_external_id == external_id
    else:
        condition = func.lower(ChatMessage.chatter_login) == login.lower()
    return external_id, login, condition


@router.get("/events/{event_id}/chat/badges")
async def site_event_chat_badges(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    await _require_public_event(db, event_id)
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
    await _require_public_event(db, event_id)

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
        "messages": [_chat_message_payload(row) for row in page],
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
    await _require_public_event(db, event_id)

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
    await _require_public_event(db, event_id)

    external_id, login, identity = _chat_identity(chatter_external_id, chatter_login)

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
    profile_row = (
        await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session.id,
                ChatMessage.is_deleted.is_(False),
                identity,
            )
            .order_by(ChatMessage.timeline_offset_ms.desc(), ChatMessage.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return {
        "event_id": str(event_id),
        "chat_session_id": str(session.id),
        "chatter_external_id": (profile_row.chatter_external_id if profile_row else None) or external_id or None,
        "chatter_login": (profile_row.chatter_login if profile_row else None) or login or None,
        "chatter_name": profile_row.chatter_name if profile_row else None,
        "color": _chat_color(profile_row) if profile_row else None,
        "badges": _chat_badges(profile_row) if profile_row else [],
        "message_count": int(count or 0),
        "first_message_ms": int(first_ms) if first_ms is not None else None,
        "last_message_ms": int(last_ms) if last_ms is not None else None,
    }


@router.get("/events/{event_id}/chat/user-messages")
async def site_event_chat_user_messages(
    event_id: uuid.UUID,
    chatter_external_id: str | None = Query(default=None, max_length=64),
    chatter_login: str | None = Query(default=None, max_length=255),
    after_ms: int | None = Query(default=None, ge=0),
    after_id: int = Query(default=0, ge=0),
    page_size: int = Query(default=500, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_public_event(db, event_id)

    external_id, login, identity = _chat_identity(chatter_external_id, chatter_login)
    primary = await _primary_chat_session(db, event_id)
    if primary is None:
        return {
            "event_id": str(event_id),
            "chat_session_id": None,
            "chatter_external_id": external_id or None,
            "chatter_login": login or None,
            "messages": [],
            "has_more": False,
            "next_cursor": None,
        }

    session, _message_count = primary
    conditions = [
        ChatMessage.session_id == session.id,
        ChatMessage.is_deleted.is_(False),
        identity,
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
        "chatter_external_id": external_id or None,
        "chatter_login": login or None,
        "messages": [_chat_message_payload(row) for row in page],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.get("/events/{event_id}/chat/user-events")
async def site_event_chat_user_events(
    event_id: uuid.UUID,
    chatter_external_id: str | None = Query(default=None, max_length=64),
    chatter_login: str | None = Query(default=None, max_length=255),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_public_event(db, event_id)

    external_id, login, identity = _chat_identity(chatter_external_id, chatter_login)
    storage_ids = await _storage_event_ids(db)
    events = []
    if storage_ids:
        events = (
            await db.execute(
                select(MediaEvent)
                .join(SiteEventPublication, SiteEventPublication.event_id == MediaEvent.id)
                .where(
                    SiteEventPublication.hidden_at_utc.is_(None),
                    MediaEvent.id.in_(storage_ids),
                )
                .order_by(MediaEvent.source_started_at_utc.desc(), MediaEvent.created_at.desc())
            )
        ).scalars().all()
    event_ids = [event.id for event in events]
    summaries = await _chat_summaries(db, event_ids)

    primary_session_by_event: dict[uuid.UUID, uuid.UUID] = {}
    for published_event in events:
        event_sessions = summaries.get(published_event.id) or []
        if event_sessions:
            primary_session_by_event[published_event.id] = uuid.UUID(str(event_sessions[0]["id"]))

    session_ids = list(primary_session_by_event.values())
    counts_by_session: dict[uuid.UUID, int] = {}
    if session_ids:
        count_rows = (
            await db.execute(
                select(ChatMessage.session_id, func.count(ChatMessage.id))
                .where(
                    ChatMessage.session_id.in_(session_ids),
                    ChatMessage.is_deleted.is_(False),
                    identity,
                )
                .group_by(ChatMessage.session_id)
            )
        ).all()
        counts_by_session = {session_id: int(count or 0) for session_id, count in count_rows}

    items = []
    for published_event in events:
        if published_event.id == event_id:
            continue
        session_id = primary_session_by_event.get(published_event.id)
        message_count = counts_by_session.get(session_id, 0) if session_id else 0
        if message_count <= 0:
            continue
        items.append(
            {
                "event_id": str(published_event.id),
                "title": _event_title(published_event),
                "channel_login": published_event.channel_login,
                "channel_display_name": published_event.channel_display_name,
                "source_started_at_utc": published_event.source_started_at_utc,
                "message_count": message_count,
            }
        )

    return {
        "event_id": str(event_id),
        "chatter_external_id": external_id or None,
        "chatter_login": login or None,
        "items": items,
    }


@router.get("/admin/events")
async def site_admin_events(db: AsyncSession = Depends(get_db)) -> dict:
    storage_ids = await _storage_event_ids(db)
    if not storage_ids:
        return {"items": []}
    events = (
        await db.execute(
            select(MediaEvent)
            .where(MediaEvent.id.in_(storage_ids))
            .order_by(MediaEvent.source_started_at_utc.desc(), MediaEvent.created_at.desc())
        )
    ).scalars().all()
    event_ids = [event.id for event in events]
    publication_rows = (
        await db.execute(
            select(SiteEventPublication).where(SiteEventPublication.event_id.in_(event_ids))
        )
    ).scalars().all()
    publications = {row.event_id: row for row in publication_rows}
    categories = await _event_categories(db, event_ids)
    assets = await _event_assets(db, event_ids)
    timecodes = await _event_timecodes(db, event_ids)
    videos = await _video_summaries(db, event_ids)
    return {
        "items": [
            {
                **_base_event_payload(event),
                "published": event.id in publications,
                "hidden": bool(publications.get(event.id) and publications[event.id].hidden_at_utc is not None),
                "categories": categories.get(event.id, []),
                "assets": _site_assets_payload(assets.get(event.id, {})),
                "timecodes": [_site_timecode_payload(row) for row in timecodes.get(event.id, [])],
                "video_sessions": len(videos.get(event.id, [])),
                "ready_parts": sum(item["ready_parts"] for item in videos.get(event.id, [])),
                "linked_parts": sum(item["linked_parts"] for item in videos.get(event.id, [])),
            }
            for event in events
        ]
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
    event_ids = [event.id for event in events]
    videos = await _video_summaries(db, event_ids)
    assets = await _event_assets(db, event_ids)
    items = []
    for event in events:
        sessions = videos.get(event.id, [])
        items.append(
            {
                **_base_event_payload(event),
                "assets": _site_assets_payload(assets.get(event.id, {})),
                "video_sessions": len(sessions),
                "ready_parts": sum(item["ready_parts"] for item in sessions),
                "linked_parts": sum(item["linked_parts"] for item in sessions),
            }
        )
    return {"items": items}


@router.put("/admin/events/{event_id}")
async def update_site_event(
    event_id: uuid.UUID,
    payload: SiteEventAdminUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    display_title = payload.display_title.strip()
    if not display_title:
        raise HTTPException(400, "display_title must not be empty")
    event.display_title = display_title
    db.add(
        AuditLog(
            event_id=event_id,
            action="site_display_title_update",
            payload_json={"display_title": display_title},
        )
    )
    await db.commit()
    return _base_event_payload(event)


@router.put("/admin/events/{event_id}/timecodes")
async def replace_site_event_timecodes(
    event_id: uuid.UUID,
    payload: SiteTimecodesRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    cleaned: list[tuple[int, str]] = []
    seen_offsets: set[int] = set()
    for item in payload.items:
        title = item.title.strip()
        if not title:
            raise HTTPException(400, "timecode title must not be empty")
        offset_ms = int(item.offset_ms)
        if offset_ms in seen_offsets:
            raise HTTPException(400, "timecode offsets must be unique within an event")
        seen_offsets.add(offset_ms)
        cleaned.append((offset_ms, title))
    cleaned.sort(key=lambda item: item[0])

    await db.execute(delete(SiteEventTimecode).where(SiteEventTimecode.event_id == event_id))
    for position, (offset_ms, title) in enumerate(cleaned, start=1):
        db.add(
            SiteEventTimecode(
                event_id=event_id,
                position=position,
                offset_ms=offset_ms,
                title=title,
            )
        )
    db.add(
        AuditLog(
            event_id=event_id,
            action="site_timecodes_replace",
            payload_json={
                "count": len(cleaned),
                "items": [{"offset_ms": offset_ms, "title": title} for offset_ms, title in cleaned],
            },
        )
    )
    await db.commit()
    rows = (await _event_timecodes(db, [event_id])).get(event_id, [])
    return {"items": [_site_timecode_payload(row) for row in rows]}


@router.put("/admin/events/{event_id}/assets/{slot}")
async def upload_site_event_asset(
    event_id: uuid.UUID,
    slot: str,
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    validate_site_asset_slot(slot)
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    prepared = await prepare_and_store_site_asset(
        image, event_id=event_id, slot=slot, settings=settings
    )
    asset = await db.get(SiteEventAsset, (event_id, slot))
    old_storage_key = asset.storage_key if asset else None
    if asset is None:
        asset = SiteEventAsset(event_id=event_id, slot=slot, storage_key=prepared.storage_key)
        db.add(asset)
    asset.storage_key = prepared.storage_key
    asset.content_type = prepared.content_type
    asset.size_bytes = prepared.size_bytes
    asset.width = prepared.width
    asset.height = prepared.height
    asset.sha256 = prepared.sha256
    asset.original_filename = prepared.original_filename
    db.add(
        AuditLog(
            event_id=event_id,
            action="site_asset_upload",
            payload_json={
                "slot": slot,
                "sha256": prepared.sha256,
                "width": prepared.width,
                "height": prepared.height,
                "size_bytes": prepared.size_bytes,
            },
        )
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if prepared.storage_key != old_storage_key:
            remove_site_asset_file(settings, prepared.storage_key)
        raise
    await db.refresh(asset)
    if old_storage_key and old_storage_key != asset.storage_key:
        remove_site_asset_file(settings, old_storage_key)
    return _site_asset_payload(asset)


@router.post("/admin/events", status_code=status.HTTP_201_CREATED)
async def publish_site_event(payload: SitePublishRequest, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, payload.event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    if await db.get(SiteEventPublication, payload.event_id) is not None:
        raise HTTPException(409, "event is already published on the site")
    if payload.event_id not in await _storage_event_ids(db):
        raise HTTPException(409, "event has no Telegram-linked ready video parts")
    assets = await _event_assets(db, [event.id])
    if not _site_assets_payload(assets.get(event.id, {}))["complete"]:
        raise HTTPException(409, "event requires one cover and exactly four preview frames before publishing")

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


@router.put("/admin/events/{event_id}/visibility")
async def update_site_event_visibility(
    event_id: uuid.UUID,
    payload: SiteVisibilityRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    publication = await db.get(SiteEventPublication, event_id)
    if publication is None:
        raise HTTPException(404, "site event not found")
    publication.hidden_at_utc = datetime.now(UTC).replace(tzinfo=None) if payload.hidden else None
    db.add(
        AuditLog(
            event_id=event_id,
            action="site_hide" if payload.hidden else "site_show",
            payload_json={"hidden": bool(payload.hidden)},
        )
    )
    await db.commit()
    return {"ok": True, "event_id": str(event_id), "hidden": bool(payload.hidden)}


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
async def hide_site_event_legacy(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Backward-compatible hide action; editorial metadata is intentionally preserved."""
    publication = await db.get(SiteEventPublication, event_id)
    if publication is None:
        return {"ok": True, "already_unpublished": True}
    publication.hidden_at_utc = datetime.now(UTC).replace(tzinfo=None)
    db.add(AuditLog(event_id=event_id, action="site_hide", payload_json={"legacy_delete_route": True}))
    await db.commit()
    return {"ok": True, "event_id": str(event_id), "hidden": True}
