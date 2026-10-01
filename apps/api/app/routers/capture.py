from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import AuditLog, MediaEvent, Session, VideoSession
from streamhub_common.settings import get_settings

from ..event_domain import resolve_or_create_media_event
from .sessions import (
    ACTIVE_CAPTURE_SESSION_STATUSES,
    SessionStartRequest,
    TwitchIntegrityContext,
    capture_progress_percent,
    resolve_live_metadata,
    session_dict,
    start_session,
)
from .video import ACTIVE_VIDEO_STATUSES, recorder_post, video_progress_percent, video_session_dict

router = APIRouter(prefix="/api/v1", tags=["capture"])
settings = get_settings()


class CaptureSelection(BaseModel):
    chat: bool = True
    video: bool = False


class CaptureStartRequest(BaseModel):
    platform: str = "twitch"
    mode: Literal["live", "vod"]
    channel_external_id: str | None = None
    channel_login: str | None = None
    channel_display_name: str | None = None
    stream_external_id: str | None = None
    video_external_id: str | None = None
    title: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    source_started_at_utc: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    page_url: str | None = None
    player_open: bool = True
    metadata: dict | None = None
    capture: CaptureSelection
    twitch_integrity: TwitchIntegrityContext | None = None


class CaptureContextRequest(BaseModel):
    mode: Literal["live", "vod"]
    channel_login: str | None = None
    video_external_id: str | None = None


def mode_result(*, requested: bool, result: str, session_id: str | None = None, error: str | None = None) -> dict:
    return {"requested": requested, "result": result, "session_id": session_id, "error": error}


async def normalize_context(payload: CaptureStartRequest) -> CaptureStartRequest:
    if not payload.player_open:
        raise HTTPException(400, "recognized player is required")
    if payload.mode == "vod":
        if not payload.video_external_id:
            raise HTTPException(400, "video_external_id is required for VOD")
        return payload
    if not payload.channel_login:
        raise HTTPException(400, "channel_login is required for LIVE")
    if payload.stream_external_id:
        return payload
    resolved, started_at = await resolve_live_metadata(payload.channel_login)
    return payload.model_copy(
        update={
            "channel_external_id": resolved.get("channel_external_id") or payload.channel_external_id,
            "channel_login": resolved.get("channel_login") or payload.channel_login,
            "channel_display_name": resolved.get("channel_display_name") or payload.channel_display_name,
            "stream_external_id": str(resolved["stream_external_id"]),
            "title": resolved.get("title") or payload.title,
            "category_id": resolved.get("category_id") or payload.category_id,
            "category_name": resolved.get("category_name") or payload.category_name,
            "source_started_at_utc": started_at or payload.source_started_at_utc,
        }
    )


async def resolve_event(payload: CaptureStartRequest, db: AsyncSession) -> MediaEvent:
    event = await resolve_or_create_media_event(
        db,
        session_id=uuid.uuid4(),
        platform=payload.platform,
        media_type=payload.mode,
        channel_external_id=payload.channel_external_id,
        channel_login=payload.channel_login,
        channel_display_name=payload.channel_display_name,
        stream_external_id=payload.stream_external_id,
        video_external_id=payload.video_external_id,
        title=payload.title,
        category_id=payload.category_id,
        category_name=payload.category_name,
        source_started_at_utc=payload.source_started_at_utc.replace(tzinfo=None) if payload.source_started_at_utc else None,
        source_duration_ms=payload.duration_ms,
        page_url=payload.page_url,
    )
    await db.commit()
    await db.refresh(event)
    return event


async def active_chat_for_event(db: AsyncSession, event_id: uuid.UUID) -> Session | None:
    return await db.scalar(
        select(Session)
        .where(
            Session.event_id == event_id,
            Session.deleted_at_utc.is_(None),
            Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
        )
        .order_by(Session.created_at.desc())
        .limit(1)
    )


async def active_video_for_event(db: AsyncSession, event_id: uuid.UUID) -> VideoSession | None:
    return await db.scalar(
        select(VideoSession)
        .where(
            VideoSession.event_id == event_id,
            VideoSession.deleted_at_utc.is_(None),
            VideoSession.status.in_(ACTIVE_VIDEO_STATUSES),
        )
        .order_by(VideoSession.created_at.desc())
        .limit(1)
    )


async def start_video_for_event(payload: CaptureStartRequest, event: MediaEvent, db: AsyncSession) -> dict:
    # Rollback expires ORM rows even with expire_on_commit=False. Snapshot every
    # value needed after a possible rollback before taking the event lock.
    event_id = event.id
    source_url = event.source_url or payload.page_url
    quality = settings.video_stream_quality

    # Serialize video Start independently from Chat Start on the canonical event row.
    await db.execute(select(MediaEvent).where(MediaEvent.id == event_id).with_for_update())
    active = await active_video_for_event(db, event_id)
    if active is not None:
        active_session_id = active.id
        await db.rollback()
        return mode_result(requested=True, result="already_active", session_id=str(active_session_id))

    if not source_url:
        await db.rollback()
        return mode_result(requested=True, result="failed", error="canonical Twitch source URL is missing")

    now = datetime.now(UTC).replace(tzinfo=None)
    video_session_id = uuid.uuid4()
    session = VideoSession(
        id=video_session_id,
        event_id=event_id,
        status="arming",
        completeness_status="collecting",
        quality=quality,
        recorder_mode="direct_hls_copy",
        source_url=source_url,
        recording_started_at_utc=now,
        required_start_ms=0,
        required_end_ms=payload.duration_ms if payload.mode == "vod" else None,
        coverage_start_ms=None,
        coverage_end_ms=None,
        gap_count=0,
        last_activity_at_utc=now,
        metadata_json={
            "media_type": payload.mode,
            "video_external_id": payload.video_external_id,
            "stream_external_id": payload.stream_external_id,
            "channel_login": payload.channel_login,
            "stage": "video_capture_core",
        },
    )
    db.add(session)
    # audit_log.video_session_id has a real FK to video_sessions.id. Flush the
    # parent row first so MySQL can validate the audit insert in the same
    # transaction instead of depending on ORM flush ordering between unrelated
    # mapped objects.
    await db.flush()
    db.add(
        AuditLog(
            event_id=event_id,
            video_session_id=video_session_id,
            action="video_start_requested",
            payload_json={"media_type": payload.mode, "quality": quality},
        )
    )
    await db.commit()

    try:
        await recorder_post(
            "/internal/v1/video-sessions",
            {
                "session_id": str(video_session_id),
                "event_id": str(event_id),
                "media_type": payload.mode,
                "source_url": source_url,
                "quality": quality,
                "source_duration_ms": payload.duration_ms,
            },
        )
    except HTTPException as exc:
        session.status = "failed"
        session.completeness_status = "failed"
        session.last_error = str(exc.detail)
        session.ended_at_utc = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
        return mode_result(requested=True, result="failed", session_id=str(video_session_id), error=str(exc.detail))

    return mode_result(requested=True, result="started", session_id=str(video_session_id))


@router.post("/capture/start")
async def start_capture(payload: CaptureStartRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if not payload.capture.chat and not payload.capture.video:
        raise HTTPException(400, "at least one capture mode must be selected")
    payload = await normalize_context(payload)
    event = await resolve_event(payload, db)
    # Keep the response independent from ORM expiration. Both Chat and Video
    # branches may rollback their transaction while still returning a useful
    # per-mode result to the extension.
    event_id = event.id
    event_snapshot = {
        "id": str(event.id),
        "media_type": event.media_type,
        "video_external_id": event.video_external_id,
        "stream_external_id": event.stream_external_id,
    }

    chat_result = mode_result(requested=False, result="not_requested")
    video_result = mode_result(requested=False, result="not_requested")

    if payload.capture.chat:
        active_chat = await active_chat_for_event(db, event_id)
        if active_chat is not None:
            chat_result = mode_result(requested=True, result="already_active", session_id=str(active_chat.id))
        elif payload.mode == "vod" and not payload.twitch_integrity:
            chat_result = mode_result(
                requested=True,
                result="failed",
                error="browser-captured Twitch Client-Integrity is required for VOD Chat",
            )
        else:
            try:
                result = await start_session(
                    SessionStartRequest(
                        platform=payload.platform,
                        mode=payload.mode,
                        channel_external_id=payload.channel_external_id,
                        channel_login=payload.channel_login,
                        channel_display_name=payload.channel_display_name,
                        stream_external_id=payload.stream_external_id,
                        video_external_id=payload.video_external_id,
                        title=payload.title,
                        category_id=payload.category_id,
                        category_name=payload.category_name,
                        source_started_at_utc=payload.source_started_at_utc,
                        duration_ms=payload.duration_ms,
                        page_url=payload.page_url,
                        player_open=payload.player_open,
                        metadata=payload.metadata,
                        twitch_integrity=payload.twitch_integrity,
                    ),
                    db,
                )
                chat_result = mode_result(requested=True, result="started", session_id=result["id"])
            except HTTPException as exc:
                await db.rollback()
                active_chat = await active_chat_for_event(db, event_id)
                if exc.status_code == 409 and active_chat is not None:
                    chat_result = mode_result(requested=True, result="already_active", session_id=str(active_chat.id))
                else:
                    chat_result = mode_result(requested=True, result="failed", error=str(exc.detail))

    if payload.capture.video:
        try:
            refreshed_event = await db.get(MediaEvent, event_id)
            assert refreshed_event is not None
            video_result = await start_video_for_event(payload, refreshed_event, db)
        except Exception as exc:
            await db.rollback()
            video_result = mode_result(requested=True, result="failed", error=str(exc))

    return {
        "event": event_snapshot,
        "chat": chat_result,
        "video": video_result,
        "ok": any(item["result"] in {"started", "already_active"} for item in (chat_result, video_result)),
    }


@router.get("/capture/progress")
async def capture_progress(
    chat_session_id: list[uuid.UUID] | None = Query(default=None),
    video_session_id: list[uuid.UUID] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    chat_ids = list(dict.fromkeys(chat_session_id or []))
    video_ids = list(dict.fromkeys(video_session_id or []))
    if len(chat_ids) + len(video_ids) > 200:
        raise HTTPException(400, "at most 200 capture session ids are allowed")

    chat_rows = []
    if chat_ids:
        chat_rows = (
            await db.execute(
                select(Session).where(Session.id.in_(chat_ids), Session.deleted_at_utc.is_(None))
            )
        ).scalars().all()
    video_rows = []
    if video_ids:
        video_rows = (
            await db.execute(
                select(VideoSession).where(VideoSession.id.in_(video_ids), VideoSession.deleted_at_utc.is_(None))
            )
        ).scalars().all()

    return {
        "chat": [
            {
                "session_id": str(row.id),
                "media_type": row.media_type,
                "status": row.status,
                "completeness_status": row.completeness_status,
                "source_duration_ms": row.source_duration_ms,
                "coverage_end_ms": row.coverage_end_ms,
                "percent": capture_progress_percent(
                    media_type=row.media_type,
                    status_value=row.status,
                    completeness_status=row.completeness_status,
                    source_duration_ms=row.source_duration_ms,
                    coverage_end_ms=row.coverage_end_ms,
                ),
            }
            for row in chat_rows
        ],
        "video": [
            {
                "session_id": str(row.id),
                "status": row.status,
                "completeness_status": row.completeness_status,
                "duration_recorded_ms": row.duration_recorded_ms,
                "coverage_end_ms": row.coverage_end_ms,
                "required_end_ms": row.required_end_ms,
                "gap_count": row.gap_count,
                "last_error": row.last_error,
                "percent": video_progress_percent(row),
            }
            for row in video_rows
        ],
        "poll_after_ms": 5000,
    }


@router.post("/capture/context-status")
async def capture_context_status(payload: CaptureContextRequest, db: AsyncSession = Depends(get_db)) -> dict:
    resolved_stream_id = None
    if payload.mode == "vod":
        if not payload.video_external_id:
            raise HTTPException(400, "video_external_id is required for VOD")
        external_key = f"twitch:vod:{payload.video_external_id}"
    else:
        if not payload.channel_login:
            raise HTTPException(400, "channel_login is required for LIVE")
        resolved, _started_at = await resolve_live_metadata(payload.channel_login)
        resolved_stream_id = str(resolved["stream_external_id"])
        external_key = f"twitch:live:{resolved_stream_id}"

    event = await db.scalar(select(MediaEvent).where(MediaEvent.external_key == external_key))
    if event is None:
        return {
            "event": None,
            "active_chat_session": None,
            "active_video_session": None,
            "resolved_stream_external_id": resolved_stream_id,
        }
    chat = await active_chat_for_event(db, event.id)
    video = await active_video_for_event(db, event.id)
    return {
        "event": {"id": str(event.id), "external_key": event.external_key, "media_type": event.media_type},
        "active_chat_session": session_dict(chat) if chat else None,
        "active_video_session": video_session_dict(video) if video else None,
        "resolved_stream_external_id": resolved_stream_id,
    }
