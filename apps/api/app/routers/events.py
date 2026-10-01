from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import ChatMessage, MediaEvent, Session

from .sessions import ACTIVE_CAPTURE_SESSION_STATUSES, capture_progress_percent, session_dict, stop_capture_row

router = APIRouter(prefix="/api/v1", tags=["events"])


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
        "category_id": row.category_id,
        "category_name": row.category_name,
        "source_started_at_utc": row.source_started_at_utc,
        "source_duration_ms": row.source_duration_ms,
        "source_url": row.source_url,
        "related_event_id": str(row.related_event_id) if row.related_event_id else None,
        "metadata": row.metadata_json,
        "deleted_at_utc": row.deleted_at_utc,
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


async def _chat_summaries_by_event(db: AsyncSession, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not event_ids:
        return {}

    sessions = (
        await db.execute(
            select(Session)
            .where(Session.event_id.in_(event_ids), Session.deleted_at_utc.is_(None))
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


def _event_payload(event: MediaEvent, chat_sessions: list[dict]) -> dict:
    active_count = sum(1 for session in chat_sessions if session["status"] in ACTIVE_CAPTURE_SESSION_STATUSES)
    return {
        **event_dict(event),
        "chat_sessions": chat_sessions,
        "chat_sessions_count": len(chat_sessions),
        "active_chat_sessions_count": active_count,
        "has_chat": bool(chat_sessions),
        "has_video": False,
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
    conditions = [MediaEvent.deleted_at_utc.is_(None), visible_chat_exists]
    if channel:
        conditions.append(MediaEvent.channel_login == channel)
    if type:
        conditions.append(MediaEvent.media_type == type)
    if title:
        conditions.append(MediaEvent.title.like(f"%{title}%"))
    if active is not None:
        active_exists = (
            select(Session.id)
            .where(
                Session.event_id == MediaEvent.id,
                Session.deleted_at_utc.is_(None),
                Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
            )
            .exists()
        )
        conditions.append(active_exists if active else ~active_exists)

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

    summaries = await _chat_summaries_by_event(db, [event.id for event in events])
    return {
        "items": [_event_payload(event, summaries.get(event.id, [])) for event in events],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


@router.get("/events/{event_id}")
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None or event.deleted_at_utc is not None:
        raise HTTPException(404, "event not found")
    summaries = await _chat_summaries_by_event(db, [event_id])
    return _event_payload(event, summaries.get(event_id, []))


@router.post("/events/{event_id}/stop-all")
async def stop_all_event_capture(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(MediaEvent, event_id)
    if event is None or event.deleted_at_utc is not None:
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

    results = []
    for row in active_rows:
        try:
            await stop_capture_row(db, row, reason="stop_all_user")
            results.append({"session_id": str(row.id), "result": "stopped", "error": None})
        except HTTPException as exc:
            results.append({"session_id": str(row.id), "result": "failed", "error": str(exc.detail)})
    await db.commit()
    return {
        "event_id": str(event_id),
        "chat": {
            "active_before": len(active_rows),
            "stopped": sum(1 for item in results if item["result"] == "stopped"),
            "failed": sum(1 for item in results if item["result"] == "failed"),
            "results": results,
        },
    }
