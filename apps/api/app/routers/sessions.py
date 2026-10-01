from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import AuditLog, CaptureJob, ChatEvent, ChatMessage, MediaEvent, Session, SessionSegment
from streamhub_common.settings import get_settings

from ..event_domain import resolve_or_create_media_event

router = APIRouter(prefix="/api/v1", tags=["sessions"])
settings = get_settings()

# Deletion is deliberately refused while a collector can still mutate session data.
# This keeps UI housekeeping completely out of the capture path.
ACTIVE_CAPTURE_SESSION_STATUSES = frozenset({"arming", "recording", "paused", "reconciling"})
ACTIVE_CAPTURE_JOB_STATUSES = frozenset({"running", "paused"})


def capture_progress_percent(
    *,
    media_type: str,
    status_value: str,
    completeness_status: str,
    source_duration_ms: int | None,
    coverage_end_ms: int | None,
) -> float | None:
    """Return a cheap UI progress estimate without touching message/event tables."""
    if completeness_status == "complete" or status_value == "completed":
        return 100.0
    if media_type != "vod" or not source_duration_ms or source_duration_ms <= 0:
        return None
    covered = max(0, min(int(coverage_end_ms or 0), int(source_duration_ms)))
    percent = round((covered / source_duration_ms) * 100.0, 1)
    # Until end-of-pagination is confirmed, never visually claim 100% complete.
    return min(percent, 99.9)


async def ensure_capture_is_idle_for_delete(db: AsyncSession, row: Session) -> None:
    if row.status in ACTIVE_CAPTURE_SESSION_STATUSES:
        raise HTTPException(409, "active or paused capture must be stopped before deleting the session")
    active_job_id = await db.scalar(
        select(CaptureJob.id)
        .where(CaptureJob.session_id == row.id, CaptureJob.status.in_(ACTIVE_CAPTURE_JOB_STATUSES))
        .limit(1)
    )
    if active_job_id is not None:
        raise HTTPException(409, "capture job must be stopped before deleting the session")


class TwitchIntegrityContext(BaseModel):
    client_integrity: str
    device_id: str
    client_id: str | None = None
    client_version: str | None = None
    client_session_id: str | None = None
    authorization: str | None = None
    user_agent: str | None = None
    captured_at: datetime | None = None


class SessionStartRequest(BaseModel):
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
    twitch_integrity: TwitchIntegrityContext | None = None


class CollectorContextRequest(BaseModel):
    mode: Literal["live", "vod"]
    channel_login: str | None = None
    video_external_id: str | None = None


class StopRequest(BaseModel):
    reason: str = "stop_user"


def session_dict(row: Session) -> dict:
    return {
        "id": str(row.id),
        "event_id": str(row.event_id),
        "platform": row.platform,
        "media_type": row.media_type,
        "status": row.status,
        "completeness_status": row.completeness_status,
        "channel_external_id": row.channel_external_id,
        "channel_login": row.channel_login,
        "channel_display_name": row.channel_display_name,
        "stream_external_id": row.stream_external_id,
        "video_external_id": row.video_external_id,
        "title": row.title,
        "category_id": row.category_id,
        "category_name": row.category_name,
        "source_started_at_utc": row.source_started_at_utc,
        "recording_started_at_utc": row.recording_started_at_utc,
        "recording_ended_at_utc": row.recording_ended_at_utc,
        "duration_recorded_ms": row.duration_recorded_ms,
        "source_duration_ms": row.source_duration_ms,
        "deleted_at_utc": row.deleted_at_utc,
        "coverage_start_ms": row.coverage_start_ms,
        "coverage_end_ms": row.coverage_end_ms,
        "gap_count": row.gap_count,
        "reconciliation_status": row.reconciliation_status,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def adapter_post(path: str, payload: dict) -> dict:
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(f"{settings.twitch_adapter_base_url}{path}", json=payload, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"twitch-adapter error: {response.text[:500]}")
    return response.json()


async def resolve_live_metadata(channel_login: str) -> tuple[dict, datetime | None]:
    resolved = await adapter_post(
        "/internal/v1/metadata/resolve",
        {"mode": "live", "channel_login": channel_login},
    )
    if not resolved.get("stream_external_id"):
        raise HTTPException(status_code=502, detail="Twitch metadata resolver returned no stream id")
    started_at = None
    if resolved.get("source_started_at_utc"):
        started_at = datetime.fromisoformat(str(resolved["source_started_at_utc"]).replace("Z", "+00:00"))
    return resolved, started_at


async def stop_capture_row(db: AsyncSession, row: Session, *, reason: str) -> None:
    if row.media_type == "vod":
        await adapter_post(f"/internal/v1/vod-fetches/{row.id}/stop", {"reason": reason})
    else:
        await adapter_post(f"/internal/v1/live-collectors/{row.id}/stop", {"reason": reason})
    if row.completeness_status != "complete":
        row.status = "stopped_incomplete"
        row.completeness_status = "incomplete"
    row.recording_ended_at_utc = datetime.now(UTC).replace(tzinfo=None)
    db.add(AuditLog(session_id=row.id, action="manual_stop", payload_json={"reason": reason}))


@router.post("/collector/context-status")
async def collector_context_status(payload: CollectorContextRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if payload.mode == "vod":
        if not payload.video_external_id:
            raise HTTPException(400, "video_external_id is required for VOD")
        external_key = f"twitch:vod:{payload.video_external_id}"
        resolved_stream_external_id = None
    else:
        if not payload.channel_login:
            raise HTTPException(400, "channel_login is required for LIVE")
        resolved, _started_at = await resolve_live_metadata(payload.channel_login)
        resolved_stream_external_id = str(resolved["stream_external_id"])
        external_key = f"twitch:live:{resolved_stream_external_id}"

    event = await db.scalar(select(MediaEvent).where(MediaEvent.external_key == external_key))
    if event is None:
        return {
            "event": None,
            "active_session": None,
            "active_sessions_count": 0,
            "resolved_stream_external_id": resolved_stream_external_id,
        }

    active_rows = (
        await db.execute(
            select(Session)
            .where(
                Session.event_id == event.id,
                Session.deleted_at_utc.is_(None),
                Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
            )
            .order_by(Session.created_at.desc())
        )
    ).scalars().all()
    return {
        "event": {"id": str(event.id), "external_key": event.external_key, "media_type": event.media_type},
        "active_session": session_dict(active_rows[0]) if active_rows else None,
        "active_sessions_count": len(active_rows),
        "resolved_stream_external_id": resolved_stream_external_id,
    }


@router.post("/collector/sessions", status_code=status.HTTP_201_CREATED)
async def start_session(payload: SessionStartRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if not payload.player_open:
        raise HTTPException(status_code=400, detail="recognized player is required")
    if payload.mode == "vod" and not payload.video_external_id:
        raise HTTPException(status_code=400, detail="video_external_id is required for VOD")
    if payload.mode == "vod" and not payload.twitch_integrity:
        raise HTTPException(
            status_code=400,
            detail="browser-captured Twitch Client-Integrity context is required for complete VOD pagination",
        )
    if payload.mode == "live" and not payload.channel_login:
        raise HTTPException(status_code=400, detail="channel_login is required for LIVE")
    if payload.mode == "live" and not payload.stream_external_id:
        resolved, resolved_started_at = await resolve_live_metadata(payload.channel_login)
        payload = payload.model_copy(
            update={
                "channel_external_id": resolved.get("channel_external_id") or payload.channel_external_id,
                "channel_login": resolved.get("channel_login") or payload.channel_login,
                "channel_display_name": resolved.get("channel_display_name") or payload.channel_display_name,
                "stream_external_id": str(resolved["stream_external_id"]),
                "title": resolved.get("title") or payload.title,
                "category_id": resolved.get("category_id") or payload.category_id,
                "category_name": resolved.get("category_name") or payload.category_name,
                "source_started_at_utc": resolved_started_at or payload.source_started_at_utc,
            }
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    session_id = uuid.uuid4()
    event = await resolve_or_create_media_event(
        db,
        session_id=session_id,
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
    # Serialize Start for the same canonical event. The DB row lock is the
    # invariant; Extension state is only a control-plane convenience.
    event = (
        await db.execute(select(MediaEvent).where(MediaEvent.id == event.id).with_for_update())
    ).scalar_one()
    active_session = await db.scalar(
        select(Session)
        .where(
            Session.event_id == event.id,
            Session.deleted_at_utc.is_(None),
            Session.status.in_(ACTIVE_CAPTURE_SESSION_STATUSES),
        )
        .order_by(Session.created_at.desc())
        .limit(1)
    )
    if active_session is not None:
        raise HTTPException(
            status_code=409,
            detail=f"active chat session already exists for event: {active_session.id}",
        )

    row = Session(
        id=session_id,
        event_id=event.id,
        platform=payload.platform,
        media_type=payload.mode,
        status="arming",
        completeness_status="collecting",
        channel_external_id=payload.channel_external_id,
        channel_login=payload.channel_login,
        channel_display_name=payload.channel_display_name,
        stream_external_id=payload.stream_external_id,
        video_external_id=payload.video_external_id,
        title=payload.title,
        category_id=payload.category_id,
        category_name=payload.category_name,
        source_started_at_utc=payload.source_started_at_utc.replace(tzinfo=None) if payload.source_started_at_utc else None,
        recording_started_at_utc=now,
        source_duration_ms=payload.duration_ms,
        metadata_json={"page_url": payload.page_url, **(payload.metadata or {})},
    )
    db.add(row)
    await db.flush()
    await db.commit()

    try:
        if payload.mode == "vod":
            await adapter_post(
                "/internal/v1/vod-fetches",
                {
                    "session_id": str(row.id),
                    "video_id": payload.video_external_id,
                    "duration_ms": payload.duration_ms,
                    "twitch_integrity": payload.twitch_integrity.model_dump(mode="json") if payload.twitch_integrity else None,
                },
            )
        else:
            await adapter_post(
                "/internal/v1/live-collectors",
                {"session_id": str(row.id), "channel_login": payload.channel_login},
            )
    except HTTPException:
        row.status = "failed"
        row.completeness_status = "failed"
        await db.commit()
        raise

    await db.refresh(row)
    return session_dict(row)


@router.post("/collector/sessions/{session_id}/pause")
async def pause_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.status in {"completed", "failed", "soft_deleted"}:
        raise HTTPException(409, f"cannot pause session in state {row.status}")
    if row.media_type == "vod":
        await adapter_post(f"/internal/v1/vod-fetches/{session_id}/pause", {})
    else:
        await adapter_post(f"/internal/v1/live-collectors/{session_id}/pause", {})
    row.status = "paused"
    db.add(AuditLog(session_id=session_id, action="pause", payload_json={}))
    await db.commit()
    return session_dict(row)


@router.post("/collector/sessions/{session_id}/resume")
async def resume_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.deleted_at_utc is not None or row.status == "soft_deleted":
        raise HTTPException(409, "cannot resume a session that is in the trash")
    if row.media_type == "vod":
        await adapter_post(f"/internal/v1/vod-fetches/{session_id}/resume", {})
    else:
        await adapter_post(f"/internal/v1/live-collectors/{session_id}/resume", {})
    row.status = "recording"
    db.add(AuditLog(session_id=session_id, action="resume", payload_json={}))
    await db.commit()
    return session_dict(row)


@router.post("/collector/sessions/{session_id}/stop")
async def stop_session(session_id: uuid.UUID, payload: StopRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    await stop_capture_row(db, row, reason=payload.reason)
    await db.commit()
    return session_dict(row)


@router.post("/collector/sessions/{session_id}/twitch-integrity")
async def update_twitch_integrity(
    session_id: uuid.UUID,
    payload: TwitchIntegrityContext,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.media_type != "vod":
        raise HTTPException(409, "Twitch integrity context is only used by VOD capture")
    await adapter_post(
        f"/internal/v1/vod-fetches/{session_id}/integrity",
        payload.model_dump(mode="json"),
    )
    return {"ok": True}


@router.post("/collector/sessions/{session_id}/heartbeat")
async def heartbeat(session_id: uuid.UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    meta = dict(row.metadata_json or {})
    meta["last_control_heartbeat"] = payload
    meta["last_control_heartbeat_at"] = datetime.now(UTC).isoformat()
    row.metadata_json = meta
    await db.commit()
    return {"ok": True, "capture_continues_without_heartbeat": True}


@router.get("/collector/sessions/{session_id}/capture-status")
async def capture_status(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    job = (
        await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id).order_by(CaptureJob.id.desc()))
    ).scalars().first()
    return {
        "session": session_dict(row),
        "job": None
        if not job
        else {
            "kind": job.job_kind,
            "status": job.status,
            "pages": job.pages_processed,
            "messages": job.messages_processed,
            "last_offset_ms": job.last_offset_ms,
            "retry_count": job.retry_count,
            "last_error": job.last_error,
            "checkpoint_present": bool(job.checkpoint_cursor),
        },
    }


@router.post("/collector/sessions/{session_id}/reconcile")
async def reconcile_session(session_id: uuid.UUID) -> dict:
    return await adapter_post(f"/internal/v1/reconcile/{session_id}", {})


@router.get("/sessions")
async def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    channel: str | None = None,
    type: Literal["live", "vod"] | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    title: str | None = None,
    deleted: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    conditions = [Session.deleted_at_utc.is_not(None) if deleted else Session.deleted_at_utc.is_(None)]
    if channel:
        conditions.append(Session.channel_login == channel)
    if type:
        conditions.append(Session.media_type == type)
    if status_filter:
        conditions.append(Session.status == status_filter)
    if title:
        conditions.append(Session.title.like(f"%{title}%"))

    total = await db.scalar(select(func.count()).select_from(Session).where(and_(*conditions)))
    rows = (
        await db.execute(
            select(Session)
            .where(and_(*conditions))
            .order_by(Session.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {"items": [session_dict(r) for r in rows], "total": int(total or 0), "page": page, "page_size": page_size}


@router.get("/sessions/capture-progress")
async def list_capture_progress(
    session_id: list[uuid.UUID] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # One tiny query for all visible sessions. The web UI polls this no faster
    # than every 5 seconds and pauses polling while the tab is hidden.
    ids = list(dict.fromkeys(session_id or []))
    if len(ids) > 100:
        raise HTTPException(400, "at most 100 session_id values are allowed")
    if not ids:
        return {"items": [], "poll_after_ms": 5000}

    rows = (
        await db.execute(
            select(
                Session.id,
                Session.media_type,
                Session.status,
                Session.completeness_status,
                Session.source_duration_ms,
                Session.coverage_end_ms,
            ).where(Session.id.in_(ids), Session.deleted_at_utc.is_(None))
        )
    ).all()

    return {
        "items": [
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
            for row in rows
        ],
        "poll_after_ms": 5000,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    msg_count = await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id))
    event_count = await db.scalar(select(func.count()).select_from(ChatEvent).where(ChatEvent.session_id == session_id))
    return {**session_dict(row), "message_count": int(msg_count or 0), "event_count": int(event_count or 0)}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: uuid.UUID,
    from_ms: int | None = Query(default=None, ge=0),
    to_ms: int | None = Query(default=None, ge=0),
    cursor: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(ChatMessage).where(ChatMessage.session_id == session_id)
    if from_ms is not None:
        q = q.where(ChatMessage.timeline_offset_ms >= from_ms)
    if to_ms is not None:
        q = q.where(ChatMessage.timeline_offset_ms <= to_ms)
    if cursor is not None:
        q = q.where(ChatMessage.id > cursor)
    q = q.order_by(ChatMessage.timeline_offset_ms, ChatMessage.sequence_no, ChatMessage.id).limit(limit + 1)
    rows = (await db.execute(q)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                "id": r.id,
                "sequence_no": r.sequence_no,
                "provider_message_id": r.provider_message_id,
                "source_kind": r.source_kind,
                "media_offset_ms": r.media_offset_ms,
                "timeline_offset_ms": r.timeline_offset_ms,
                "effective_message_time_utc": r.effective_message_time_utc,
                "timestamp_source": r.timestamp_source,
                "chatter_external_id": r.chatter_external_id,
                "chatter_login": r.chatter_login,
                "chatter_name": r.chatter_name,
                "color": r.color,
                "badges": r.badges_json,
                "message_text": r.message_text,
                "fragments": r.fragments_json,
                "reply": r.reply_json,
                "bits": r.bits,
                "is_action": r.is_action,
                "is_deleted": r.is_deleted,
                "message_type": r.message_type,
            }
            for r in rows
        ],
        "next_cursor": rows[-1].id if has_more and rows else None,
    }


@router.get("/sessions/{session_id}/events")
async def get_events(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    rows = (
        await db.execute(
            select(ChatEvent).where(ChatEvent.session_id == session_id).order_by(ChatEvent.timeline_offset_ms, ChatEvent.sequence_no)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "sequence_no": r.sequence_no,
                "event_type": r.event_type,
                "timeline_offset_ms": r.timeline_offset_ms,
                "provider_event_id": r.provider_event_id,
                "source_kind": r.source_kind,
                "payload": r.payload_json,
            }
            for r in rows
        ]
    }


@router.delete("/sessions/{session_id}")
async def soft_delete_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.deleted_at_utc is not None:
        return {"ok": True, "already_deleted": True}
    await ensure_capture_is_idle_for_delete(db, row)
    row.deleted_at_utc = datetime.now(UTC).replace(tzinfo=None)
    row.status = "soft_deleted"
    db.add(AuditLog(session_id=session_id, action="soft_delete", payload_json={}))
    await db.commit()
    return {"ok": True}


@router.post("/sessions/{session_id}/restore")
async def restore_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.deleted_at_utc is None:
        return {"ok": True, "already_restored": True}
    row.deleted_at_utc = None
    row.status = "completed" if row.completeness_status == "complete" else "stopped_incomplete"
    db.add(AuditLog(session_id=session_id, action="restore", payload_json={}))
    await db.commit()
    return {"ok": True}


@router.delete("/deleted/sessions/{session_id}")
async def purge_session(
    session_id: uuid.UUID,
    permanent: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not permanent:
        raise HTTPException(400, "permanent=true and explicit UI confirmation are required")
    row = await db.get(Session, session_id)
    if not row:
        raise HTTPException(404, "session not found")
    if row.deleted_at_utc is None:
        raise HTTPException(409, "session must be soft-deleted first")
    await ensure_capture_is_idle_for_delete(db, row)

    # Hard purge intentionally leaves no session-scoped audit trail. Child
    # tables are deleted explicitly in the same transaction even though most
    # FKs also have ON DELETE CASCADE. audit_log uses SET NULL, so it must be
    # removed before the session row to avoid orphaned traces.
    await db.execute(delete(AuditLog).where(AuditLog.session_id == session_id))
    await db.execute(delete(ChatEvent).where(ChatEvent.session_id == session_id))
    await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.execute(delete(SessionSegment).where(SessionSegment.session_id == session_id))
    event_id = row.event_id
    await db.execute(delete(CaptureJob).where(CaptureJob.session_id == session_id))
    await db.execute(delete(Session).where(Session.id == session_id))
    remaining_sessions = await db.scalar(
        select(func.count()).select_from(Session).where(Session.event_id == event_id)
    )
    if int(remaining_sessions or 0) == 0:
        await db.execute(delete(MediaEvent).where(MediaEvent.id == event_id))
    await db.commit()
    return {"ok": True, "purged": True}
