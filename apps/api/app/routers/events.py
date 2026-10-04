from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.settings import get_settings
from streamhub_common.models import (
    AuditLog,
    CaptureJob,
    ChatEvent,
    ChatMessage,
    EventCategory,
    MediaEvent,
    Session,
    SessionSegment,
    SiteEventAsset,
    SiteEventPublication,
    SiteEventTimecode,
    VideoSegment,
    VideoSession,
)

from .sessions import (
    ACTIVE_CAPTURE_SESSION_STATUSES,
    capture_progress_percent,
    ensure_capture_is_idle_for_delete,
    session_dict,
    stop_capture_row,
)
from .video import (
    ACTIVE_VIDEO_STATUSES,
    delete_video_db_rows,
    ensure_video_idle_for_delete,
    finalize_video_purge,
    quarantine_video_for_purge,
    restore_video_purge,
    restored_video_status,
    stop_video_capture_row,
    video_progress_percent,
    video_session_dict,
)
from ..site_assets import remove_site_asset_file

router = APIRouter(prefix="/api/v1", tags=["events"])
settings = get_settings()


def event_dict(row: MediaEvent) -> dict:
    return {
        "id": str(row.id),
        "platform": row.platform,
        "media_type": row.media_type,
        "external_key": row.external_key,
        "channel_external_id": row.channel_external_id,
        "channel_login": row.channel_login,
        "channel_display_name": row.channel_display_name,
        "stream_external_id": row.stream_external_id,
        "video_external_id": row.video_external_id,
        "title": row.title,
        "display_title": row.display_title,
        "category_id": row.category_id,
        "category_name": row.category_name,
        "source_started_at_utc": row.source_started_at_utc,
        "source_duration_ms": row.source_duration_ms,
        "source_url": row.source_url,
        "related_event_id": str(row.related_event_id) if row.related_event_id else None,
        "metadata": row.metadata_json,
        "deleted_at_utc": row.deleted_at_utc,
        "deletion_group_id": str(row.deletion_group_id) if row.deletion_group_id else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def chat_session_summary(row: Session, message_count: int) -> dict:
    data = session_dict(row)
    data["message_count"] = int(message_count)
    data["progress_percent"] = capture_progress_percent(
        media_type=row.media_type,
        status_value=row.status,
        completeness_status=row.completeness_status,
        source_duration_ms=row.source_duration_ms,
        coverage_end_ms=row.coverage_end_ms,
    )
    return data


async def _chat_summaries_by_event(
    db: AsyncSession,
    event_ids: list[uuid.UUID],
    *,
    deleted: bool = False,
) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}

    conditions = [
        Session.event_id.in_(event_ids),
        Session.deleted_at_utc.is_not(None) if deleted else Session.deleted_at_utc.is_(None),
    ]
    sessions = (
        await db.execute(
            select(Session)
            .where(*conditions)
            .order_by(Session.event_id, Session.created_at.asc())
        )
    ).scalars().all()
    if not sessions:
        return {}

    session_ids = [session.id for session in sessions]
    count_rows = (
        await db.execute(
            select(ChatMessage.session_id, func.count(ChatMessage.id))
            .where(ChatMessage.session_id.in_(session_ids))
            .group_by(ChatMessage.session_id)
        )
    ).all()
    message_counts = {session_id: int(message_count or 0) for session_id, message_count in count_rows}

    grouped: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for session in sessions:
        grouped[session.event_id].append(chat_session_summary(session, message_counts.get(session.id, 0)))
    return grouped


async def _video_summaries_by_event(
    db: AsyncSession,
    event_ids: list[uuid.UUID],
    *,
    deleted: bool = False,
) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}
    conditions = [
        VideoSession.event_id.in_(event_ids),
        VideoSession.deleted_at_utc.is_not(None) if deleted else VideoSession.deleted_at_utc.is_(None),
    ]
    sessions = (
        await db.execute(
            select(VideoSession)
            .where(*conditions)
            .order_by(VideoSession.event_id, VideoSession.created_at.asc())
        )
    ).scalars().all()
    if not sessions:
        return {}

    ids = [row.id for row in sessions]
    aggregate_rows = (
        await db.execute(
            select(
                VideoSegment.video_session_id,
                func.count(VideoSegment.id),
                func.coalesce(func.sum(VideoSegment.bytes), 0),
            )
            .where(VideoSegment.video_session_id.in_(ids))
            .group_by(VideoSegment.video_session_id)
        )
    ).all()
    aggregates = {session_id: (int(count or 0), int(total_bytes or 0)) for session_id, count, total_bytes in aggregate_rows}

    grouped: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for row in sessions:
        segment_count, total_bytes = aggregates.get(row.id, (0, 0))
        payload = video_session_dict(row)
        payload["segment_count"] = segment_count
        payload["bytes"] = total_bytes
        payload["progress_percent"] = video_progress_percent(row)
        grouped[row.event_id].append(payload)
    return grouped


def _event_payload(event: MediaEvent, chat_sessions: list[dict], video_sessions: list[dict]) -> dict:
    active_chat_count = sum(1 for session in chat_sessions if session["status"] in ACTIVE_CAPTURE_SESSION_STATUSES)
    active_video_count = sum(1 for session in video_sessions if session["status"] in ACTIVE_VIDEO_STATUSES)
    return {
        **event_dict(event),
        "chat_sessions": chat_sessions,
        "video_sessions": video_sessions,
        "chat_sessions_count": len(chat_sessions),
        "video_sessions_count": len(video_sessions),
        "active_chat_sessions_count": active_chat_count,
        "active_video_sessions_count": active_video_count,
        "has_chat": bool(chat_sessions),
        "has_video": bool(video_sessions),
    }


@router.get("/events")
async def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    channel: str | None = None,
    type: Literal["live", "vod"] | None = None,
    title: str | None = None,
    active: bool | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    visible_chat_exists = (
        select(Session.id)
        .where(Session.event_id == MediaEvent.id, Session.deleted_at_utc.is_(None))
        .exists()
    )
    visible_video_exists = (
        select(VideoSession.id)
        .where(VideoSession.event_id == MediaEvent.id, VideoSession.deleted_at_utc.is_(None))
        .exists()
    )
    conditions = [visible_chat_exists | visible_video_exists]
    if channel:
        conditions.append(MediaEvent.channel_login == channel)
    if type:
        conditions.append(MediaEvent.media_type == type)
    if title:
        conditions.append(MediaEvent.title.like(f"%{title}%"))
    if active is not None:
        active_chat_exists = (
            select(Session.id)
            .where(
                Session.event_id == MediaEvent.id,
                Session.deleted_at_utc.is_(None),
                Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
            )
            .exists()
        )
        active_video_exists = (
            select(VideoSession.id)
            .where(
                VideoSession.event_id == MediaEvent.id,
                VideoSession.deleted_at_utc.is_(None),
                VideoSession.status.in_(ACTIVE_VIDEO_STATUSES),
            )
            .exists()
        )
        any_active = active_chat_exists | active_video_exists
        conditions.append(any_active if active else ~any_active)

    where_clause = and_(*conditions)
    total = await db.scalar(select(func.count()).select_from(MediaEvent).where(where_clause))
    events = (
        await db.execute(
            select(MediaEvent)
            .where(where_clause)
            .order_by(MediaEvent.created_at.desc(), MediaEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    event_ids = [event.id for event in events]
    chat_summaries = await _chat_summaries_by_event(db, event_ids)
    video_summaries = await _video_summaries_by_event(db, event_ids)
    return {
        "items": [
            _event_payload(event, chat_summaries.get(event.id, []), video_summaries.get(event.id, []))
            for event in events
        ],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


@router.get("/deleted/events")
async def list_deleted_events(
    page_size: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deleted_chat_exists = (
        select(Session.id)
        .where(Session.event_id == MediaEvent.id, Session.deleted_at_utc.is_not(None))
        .exists()
    )
    deleted_video_exists = (
        select(VideoSession.id)
        .where(VideoSession.event_id == MediaEvent.id, VideoSession.deleted_at_utc.is_not(None))
        .exists()
    )
    events = (
        await db.execute(
            select(MediaEvent)
            .where(deleted_chat_exists | deleted_video_exists)
            .order_by(MediaEvent.created_at.desc(), MediaEvent.id.desc())
            .limit(page_size)
        )
    ).scalars().all()
    event_ids = [event.id for event in events]
    chat_summaries = await _chat_summaries_by_event(db, event_ids, deleted=True)
    video_summaries = await _video_summaries_by_event(db, event_ids, deleted=True)
    return {
        "items": [
            _event_payload(event, chat_summaries.get(event.id, []), video_summaries.get(event.id, []))
            for event in events
        ]
    }


@router.get("/events/{event_id}")
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    chat_summaries = await _chat_summaries_by_event(db, [event_id])
    video_summaries = await _video_summaries_by_event(db, [event_id])
    return _event_payload(event, chat_summaries.get(event_id, []), video_summaries.get(event_id, []))


@router.post("/events/{event_id}/stop-all")
async def stop_all_event_capture(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    active_rows = (
        await db.execute(
            select(Session)
            .where(
                Session.event_id == event_id,
                Session.deleted_at_utc.is_(None),
                Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
            )
            .order_by(Session.created_at.desc())
        )
    ).scalars().all()

    chat_results = []
    for row in active_rows:
        try:
            await stop_capture_row(db, row, reason="stop_all_user")
            chat_results.append({"session_id": str(row.id), "result": "stopped", "error": None})
        except HTTPException as exc:
            chat_results.append({"session_id": str(row.id), "result": "failed", "error": str(exc.detail)})

    active_video_rows = (
        await db.execute(
            select(VideoSession)
            .where(
                VideoSession.event_id == event_id,
                VideoSession.deleted_at_utc.is_(None),
                VideoSession.status.in_(ACTIVE_VIDEO_STATUSES),
            )
            .order_by(VideoSession.created_at.desc())
        )
    ).scalars().all()
    video_results = []
    for row in active_video_rows:
        try:
            await stop_video_capture_row(db, row, reason="stop_all_user")
            video_results.append({"session_id": str(row.id), "result": "stopped", "error": None})
        except HTTPException as exc:
            video_results.append({"session_id": str(row.id), "result": "failed", "error": str(exc.detail)})
    await db.commit()
    return {
        "event_id": str(event_id),
        "chat": {
            "active_before": len(active_rows),
            "stopped": sum(1 for item in chat_results if item["result"] == "stopped"),
            "failed": sum(1 for item in chat_results if item["result"] == "failed"),
            "results": chat_results,
        },
        "video": {
            "active_before": len(active_video_rows),
            "stopped": sum(1 for item in video_results if item["result"] == "stopped"),
            "failed": sum(1 for item in video_results if item["result"] == "failed"),
            "results": video_results,
        },
    }


@router.delete("/events/{event_id}")
async def soft_delete_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Move the currently visible sessions of one canonical Event to Trash.

    MediaEvent is an identity/grouping row and is never soft-deleted. A later
    capture for the same Twitch stream/VOD therefore appears immediately in
    Events while the older deleted sessions remain grouped in Trash.
    """
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    chat_rows = (
        await db.execute(
            select(Session).where(Session.event_id == event_id, Session.deleted_at_utc.is_(None))
        )
    ).scalars().all()
    video_rows = (
        await db.execute(
            select(VideoSession).where(VideoSession.event_id == event_id, VideoSession.deleted_at_utc.is_(None))
        )
    ).scalars().all()
    if not chat_rows and not video_rows:
        return {"ok": True, "already_empty": True, "chat_sessions": 0, "video_sessions": 0}

    for row in chat_rows:
        await ensure_capture_is_idle_for_delete(db, row)
    for row in video_rows:
        await ensure_video_idle_for_delete(db, row)

    now = datetime.now(UTC).replace(tzinfo=None)
    deletion_group_id = uuid.uuid4()
    for row in chat_rows:
        row.deleted_at_utc = now
        row.deletion_group_id = deletion_group_id
        row.status = "soft_deleted"
    for row in video_rows:
        row.deleted_at_utc = now
        row.deletion_group_id = deletion_group_id
        row.status = "soft_deleted"
    # Legacy builds soft-deleted the identity row itself. Keep it normalized so
    # Events/Trash are projections over sessions only.
    event.deleted_at_utc = None
    event.deletion_group_id = None
    db.add(
        AuditLog(
            event_id=event_id,
            action="event_sessions_soft_delete",
            payload_json={
                "deletion_group_id": str(deletion_group_id),
                "chat_sessions": len(chat_rows),
                "video_sessions": len(video_rows),
            },
        )
    )
    await db.commit()
    return {
        "ok": True,
        "deletion_group_id": str(deletion_group_id),
        "chat_sessions": len(chat_rows),
        "video_sessions": len(video_rows),
    }


@router.post("/events/{event_id}/restore")
async def restore_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Restore every trashed session grouped by this canonical Event."""
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    chat_rows = (
        await db.execute(
            select(Session).where(Session.event_id == event_id, Session.deleted_at_utc.is_not(None))
        )
    ).scalars().all()
    video_rows = (
        await db.execute(
            select(VideoSession).where(VideoSession.event_id == event_id, VideoSession.deleted_at_utc.is_not(None))
        )
    ).scalars().all()

    for row in chat_rows:
        row.deleted_at_utc = None
        row.deletion_group_id = None
        if row.completeness_status == "complete":
            row.status = "completed"
        elif row.completeness_status == "failed":
            row.status = "failed"
        else:
            row.status = "stopped_incomplete"
    for row in video_rows:
        row.deleted_at_utc = None
        row.deletion_group_id = None
        row.status = restored_video_status(row)

    event.deleted_at_utc = None
    event.deletion_group_id = None
    db.add(
        AuditLog(
            event_id=event_id,
            action="event_sessions_restore",
            payload_json={"chat_sessions": len(chat_rows), "video_sessions": len(video_rows)},
        )
    )
    await db.commit()
    return {
        "ok": True,
        "restored_chat_sessions": len(chat_rows),
        "restored_video_sessions": len(video_rows),
    }


async def _event_has_any_sessions(db: AsyncSession, event_id: uuid.UUID) -> bool:
    chat_exists = (
        await db.execute(select(Session.id).where(Session.event_id == event_id).limit(1))
    ).first() is not None
    if chat_exists:
        return True
    return (
        await db.execute(select(VideoSession.id).where(VideoSession.event_id == event_id).limit(1))
    ).first() is not None


async def _clear_orphaned_site_editorial_state(
    db: AsyncSession, event: MediaEvent
) -> tuple[list[str], dict]:
    """Remove site-only customization once a canonical Event has no sessions left."""
    if await _event_has_any_sessions(db, event.id):
        return [], {"cleared": False, "reason": "event_has_remaining_sessions"}

    assets = (
        await db.execute(
            select(SiteEventAsset).where(SiteEventAsset.event_id == event.id)
        )
    ).scalars().all()
    storage_keys = [row.storage_key for row in assets if row.storage_key]
    timecode_count = int(
        (
            await db.scalar(
                select(func.count()).select_from(SiteEventTimecode).where(SiteEventTimecode.event_id == event.id)
            )
        )
        or 0
    )
    category_count = int(
        (
            await db.scalar(
                select(func.count()).select_from(EventCategory).where(EventCategory.event_id == event.id)
            )
        )
        or 0
    )
    had_publication = await db.get(SiteEventPublication, event.id) is not None

    await db.execute(delete(SiteEventTimecode).where(SiteEventTimecode.event_id == event.id))
    await db.execute(delete(EventCategory).where(EventCategory.event_id == event.id))
    await db.execute(delete(SiteEventAsset).where(SiteEventAsset.event_id == event.id))
    await db.execute(delete(SiteEventPublication).where(SiteEventPublication.event_id == event.id))
    event.display_title = event.title

    payload = {
        "cleared": True,
        "assets": len(storage_keys),
        "timecodes": timecode_count,
        "categories": category_count,
        "publication": had_publication,
        "display_title_reset": True,
    }
    db.add(AuditLog(event_id=event.id, action="site_editorial_cleanup", payload_json=payload))
    return storage_keys, payload


@router.delete("/deleted/events/{event_id}")
async def purge_event(
    event_id: uuid.UUID,
    permanent: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Permanently purge only this Event's sessions that are currently in Trash.

    Visible sessions and the canonical MediaEvent identity are intentionally kept.
    """
    if not permanent:
        raise HTTPException(400, "permanent=true and explicit UI confirmation are required")
    event = await db.get(MediaEvent, event_id)
    if event is None:
        raise HTTPException(404, "event not found")

    chat_rows = (
        await db.execute(
            select(Session).where(Session.event_id == event_id, Session.deleted_at_utc.is_not(None))
        )
    ).scalars().all()
    video_rows = (
        await db.execute(
            select(VideoSession).where(VideoSession.event_id == event_id, VideoSession.deleted_at_utc.is_not(None))
        )
    ).scalars().all()
    if not chat_rows and not video_rows:
        storage_keys, site_cleanup = await _clear_orphaned_site_editorial_state(db, event)
        await db.commit()
        for storage_key in storage_keys:
            remove_site_asset_file(settings, storage_key)
        return {
            "ok": True,
            "already_purged": True,
            "video_sessions": 0,
            "chat_sessions": 0,
            "site_cleanup": site_cleanup,
            "site_asset_files_cleanup_requested": len(storage_keys),
        }

    for row in chat_rows:
        await ensure_capture_is_idle_for_delete(db, row)
    for row in video_rows:
        await ensure_video_idle_for_delete(db, row)

    tickets: list[dict] = []
    try:
        for row in video_rows:
            tickets.append(await quarantine_video_for_purge(row))
    except Exception:
        for ticket in reversed(tickets):
            try:
                await restore_video_purge(ticket)
            except Exception:
                pass
        raise

    try:
        for row in video_rows:
            await delete_video_db_rows(db, row.id)
        chat_ids = [row.id for row in chat_rows]
        if chat_ids:
            await db.execute(delete(AuditLog).where(AuditLog.session_id.in_(chat_ids)))
            await db.execute(delete(ChatEvent).where(ChatEvent.session_id.in_(chat_ids)))
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(chat_ids)))
            await db.execute(delete(SessionSegment).where(SessionSegment.session_id.in_(chat_ids)))
            await db.execute(delete(CaptureJob).where(CaptureJob.session_id.in_(chat_ids)))
            await db.execute(delete(Session).where(Session.id.in_(chat_ids)))
        storage_keys, site_cleanup = await _clear_orphaned_site_editorial_state(db, event)
        event.deleted_at_utc = None
        event.deletion_group_id = None
        db.add(
            AuditLog(
                event_id=event_id,
                action="event_trash_purge",
                payload_json={
                    "chat_sessions": len(chat_rows),
                    "video_sessions": len(video_rows),
                    "site_cleanup": site_cleanup,
                },
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        for ticket in reversed(tickets):
            try:
                await restore_video_purge(ticket)
            except Exception:
                pass
        raise

    for storage_key in storage_keys:
        remove_site_asset_file(settings, storage_key)

    cleanup_pending = 0
    for ticket in tickets:
        try:
            await finalize_video_purge(ticket)
        except HTTPException:
            cleanup_pending += 1
    return {
        "ok": True,
        "purged": True,
        "video_sessions": len(video_rows),
        "chat_sessions": len(chat_rows),
        "filesystem_cleanup_pending": cleanup_pending,
        "site_cleanup": site_cleanup,
        "site_asset_files_cleanup_requested": len(storage_keys),
    }
