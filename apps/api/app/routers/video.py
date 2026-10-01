from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import AuditLog, VideoRun, VideoSegment, VideoSession
from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["video"])
settings = get_settings()

ACTIVE_VIDEO_STATUSES = frozenset({"arming", "recording", "reconnecting"})


def video_progress_percent(row: VideoSession) -> float | None:
    if row.completeness_status == "complete":
        return 100.0
    media_type = (row.metadata_json or {}).get("media_type")
    if media_type != "vod" or not row.required_end_ms or row.required_end_ms <= 0:
        return None
    covered = max(0, min(int(row.coverage_end_ms or 0), int(row.required_end_ms)))
    return min(99.9, round((covered / row.required_end_ms) * 100.0, 1))


def video_session_dict(row: VideoSession) -> dict:
    return {
        "id": str(row.id),
        "event_id": str(row.event_id),
        "status": row.status,
        "completeness_status": row.completeness_status,
        "quality": row.quality,
        "recorder_mode": row.recorder_mode,
        "source_url": row.source_url,
        "recording_started_at_utc": row.recording_started_at_utc,
        "ended_at_utc": row.ended_at_utc,
        "duration_recorded_ms": row.duration_recorded_ms,
        "required_start_ms": row.required_start_ms,
        "required_end_ms": row.required_end_ms,
        "coverage_start_ms": row.coverage_start_ms,
        "coverage_end_ms": row.coverage_end_ms,
        "gap_count": row.gap_count,
        "last_error": row.last_error,
        "last_activity_at_utc": row.last_activity_at_utc,
        "stop_reason": row.stop_reason,
        "metadata": row.metadata_json,
        "deleted_at_utc": row.deleted_at_utc,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "progress_percent": video_progress_percent(row),
    }


async def recorder_post(path: str, payload: dict) -> dict:
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{settings.video_recorder_base_url}{path}", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"video-recorder unavailable: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"video-recorder error: {response.text[:500]}")
    return response.json()


async def stop_video_capture_row(db: AsyncSession, row: VideoSession, *, reason: str) -> None:
    await recorder_post(f"/internal/v1/video-sessions/{row.id}/stop", {"reason": reason})
    await db.refresh(row)
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_manual_stop",
            payload_json={"reason": reason},
        )
    )


@router.post("/video-sessions/{session_id}/stop")
async def stop_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    if row.status not in ACTIVE_VIDEO_STATUSES:
        return video_session_dict(row)
    await stop_video_capture_row(db, row, reason="user_stop")
    await db.commit()
    await db.refresh(row)
    return video_session_dict(row)


@router.get("/video-sessions/capture-progress")
async def video_capture_progress(
    session_id: list[uuid.UUID] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ids = list(dict.fromkeys(session_id or []))
    if len(ids) > 100:
        raise HTTPException(400, "at most 100 session_id values are allowed")
    if not ids:
        return {"items": [], "poll_after_ms": 5000}
    rows = (
        await db.execute(
            select(VideoSession).where(VideoSession.id.in_(ids), VideoSession.deleted_at_utc.is_(None))
        )
    ).scalars().all()
    return {
        "items": [
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
            for row in rows
        ],
        "poll_after_ms": 5000,
    }


@router.get("/video-sessions/{session_id}")
async def get_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    segment_count, total_bytes = (
        await db.execute(
            select(func.count(VideoSegment.id), func.coalesce(func.sum(VideoSegment.bytes), 0)).where(
                VideoSegment.video_session_id == session_id
            )
        )
    ).one()
    state_rows = (
        await db.execute(
            select(
                VideoSegment.storage_state,
                func.count(VideoSegment.id),
                func.coalesce(func.sum(VideoSegment.bytes), 0),
            )
            .where(VideoSegment.video_session_id == session_id)
            .group_by(VideoSegment.storage_state)
        )
    ).all()
    storage_summary = {
        state: {"segments": int(count or 0), "bytes": int(size or 0)}
        for state, count, size in state_rows
    }
    metadata = row.metadata_json or {}
    output_subdir = str(metadata.get("output_subdir") or "streamhub").strip("/\\")
    relative_root = f"{output_subdir}/twitch/events/{row.event_id}/video/{row.id}"
    return {
        **video_session_dict(row),
        "segment_count": int(segment_count or 0),
        "bytes": int(total_bytes or 0),
        "output_root_key": str(metadata.get("output_root_key") or "root1"),
        "output_subdir": output_subdir,
        "archive_relative_root": relative_root,
        "storage_summary": storage_summary,
    }


@router.get("/video-sessions/{session_id}/runs")
async def list_video_runs(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    runs = (
        await db.execute(
            select(VideoRun).where(VideoRun.video_session_id == session_id).order_by(VideoRun.run_no)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": run.id,
                "run_no": run.run_no,
                "status": run.status,
                "started_at_utc": run.started_at_utc,
                "ended_at_utc": run.ended_at_utc,
                "resume_source_offset_ms": run.resume_source_offset_ms,
                "first_segment_no": run.first_segment_no,
                "last_segment_no": run.last_segment_no,
                "streamlink_exit_code": run.streamlink_exit_code,
                "ffmpeg_exit_code": run.ffmpeg_exit_code,
                "close_reason": run.close_reason,
                "last_error": run.last_error,
            }
            for run in runs
        ]
    }


@router.get("/video-sessions/{session_id}/segments")
async def list_video_segments(
    session_id: uuid.UUID,
    after_segment_no: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    segments = (
        await db.execute(
            select(VideoSegment)
            .where(VideoSegment.video_session_id == session_id, VideoSegment.segment_no > after_segment_no)
            .order_by(VideoSegment.segment_no)
            .limit(page_size + 1)
        )
    ).scalars().all()
    has_more = len(segments) > page_size
    segments = segments[:page_size]
    return {
        "items": [
            {
                "id": segment.id,
                "segment_no": segment.segment_no,
                "video_run_id": segment.video_run_id,
                "file_name": segment.file_name,
                "relative_path": segment.relative_path,
                "timeline_start_ms": segment.timeline_start_ms,
                "timeline_end_ms": segment.timeline_end_ms,
                "source_media_start_ms": segment.source_media_start_ms,
                "source_media_end_ms": segment.source_media_end_ms,
                "duration_ms": segment.duration_ms,
                "bytes": segment.bytes,
                "storage_state": segment.storage_state,
                "integrity_state": segment.integrity_state,
                "sha256": segment.sha256,
                "archive_attempts": segment.archive_attempts,
                "archive_last_error": segment.archive_last_error,
                "archived_at_utc": segment.archived_at_utc,
                "closed_at_utc": segment.closed_at_utc,
            }
            for segment in segments
        ],
        "next_after_segment_no": segments[-1].segment_no if has_more and segments else None,
    }
