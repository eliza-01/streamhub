from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import (
    AuditLog,
    MediaEvent,
    StorageMigrationJob,
    VideoGap,
    VideoPart,
    VideoPartBuildJob,
    VideoPartSegment,
    VideoRun,
    VideoSegment,
    VideoSession,
)
from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["video"])
settings = get_settings()

RUNNING_VIDEO_STATUSES = frozenset({"arming", "recording", "reconnecting"})
ACTIVE_VIDEO_STATUSES = RUNNING_VIDEO_STATUSES | {"paused"}

PART_ACTIVE_JOB_STATUSES = frozenset({"queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"})


class PartPlanRequest(BaseModel):
    mode: str = "manual"
    from_segment_no: int = Field(ge=1)
    to_segment_no: int | None = Field(default=None, ge=1)
    target_mib: int | None = Field(default=None, ge=1, le=102400)


class PartCreateRequest(PartPlanRequest):
    pass


async def part_builder_request(method: str, path: str, payload: dict | None = None, *, timeout: float = 30.0) -> dict:
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{settings.video_part_builder_base_url}{path}",
                json=payload if payload is not None else {},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"video-part-builder unavailable: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        detail = response.text[:1000]
        status_code = 409 if response.status_code == 409 else 502
        raise HTTPException(status_code=status_code, detail=f"video-part-builder error: {detail}")
    return response.json()


async def part_builder_post(path: str, payload: dict | None = None, *, timeout: float = 30.0) -> dict:
    return await part_builder_request("POST", path, payload, timeout=timeout)


async def part_builder_delete(path: str, *, timeout: float = 30.0) -> dict:
    return await part_builder_request("DELETE", path, {}, timeout=timeout)


def part_dict(part: VideoPart, job: VideoPartBuildJob | None = None) -> dict:
    return {
        "id": str(part.id),
        "video_session_id": str(part.video_session_id),
        "part_no": part.part_no,
        "run_no": part.run_no,
        "start_segment_no": part.start_segment_no,
        "end_segment_no": part.end_segment_no,
        "duration_ms": part.duration_ms,
        "expected_bytes": part.expected_bytes,
        "final_bytes": part.final_bytes,
        "sha256": part.sha256,
        "file_name": part.file_name,
        "relative_path": part.relative_path,
        "status": part.status,
        "last_error": part.last_error,
        "created_at": part.created_at,
        "completed_at_utc": part.completed_at_utc,
        "job": None if job is None else {
            "id": job.id,
            "status": job.status,
            "phase": job.phase,
            "attempts": job.attempts,
            "cancel_requested": job.cancel_requested,
            "progress_bytes": job.progress_bytes,
            "total_bytes": job.total_bytes,
            "last_error": job.last_error,
            "created_at": job.created_at,
            "started_at_utc": job.started_at_utc,
            "completed_at_utc": job.completed_at_utc,
            "heartbeat_at_utc": job.heartbeat_at_utc,
        },
    }


def _part_file_name(session_id: uuid.UUID, part_no: int, start_segment_no: int, end_segment_no: int) -> str:
    return f"vp_{session_id.hex}__part{part_no:04d}__s{start_segment_no:06d}-s{end_segment_no:06d}.ts"


async def plan_part_range(
    db: AsyncSession,
    session_id: uuid.UUID,
    payload: PartPlanRequest,
    *,
    lock: bool = False,
) -> tuple[VideoSession, list[VideoSegment], int, dict]:
    session = await db.get(VideoSession, session_id)
    if session is None or session.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    if payload.mode not in {"manual", "target"}:
        raise HTTPException(400, "mode must be manual or target")
    if payload.mode == "manual" and payload.to_segment_no is None:
        raise HTTPException(400, "to_segment_no is required for manual mode")
    if payload.mode == "manual" and int(payload.to_segment_no or 0) < payload.from_segment_no:
        raise HTTPException(400, "to_segment_no must be >= from_segment_no")

    stmt = (
        select(VideoSegment, VideoRun.run_no)
        .join(VideoRun, VideoRun.id == VideoSegment.video_run_id)
        .where(
            VideoSegment.video_session_id == session_id,
            VideoSegment.segment_no >= payload.from_segment_no,
        )
        .order_by(VideoSegment.segment_no)
        .limit(10000)
    )
    if payload.mode == "manual":
        stmt = stmt.where(VideoSegment.segment_no <= int(payload.to_segment_no))
    if lock:
        stmt = stmt.with_for_update()
    rows = (await db.execute(stmt)).all()
    if not rows or rows[0][0].segment_no != payload.from_segment_no:
        raise HTTPException(409, "from_segment_no is missing or not available")

    segment_ids = [segment.id for segment, _run_no in rows]
    reserved_ids = set()
    if segment_ids:
        reserved_ids = set(
            (
                await db.execute(
                    select(VideoPartSegment.segment_id).where(VideoPartSegment.segment_id.in_(segment_ids))
                )
            ).scalars().all()
        )

    selected: list[VideoSegment] = []
    run_no: int | None = None
    expected_next = payload.from_segment_no
    total_bytes = 0
    total_duration = 0
    warnings: list[str] = []
    target_bytes = int((payload.target_mib or settings.part_build_target_mib_default) * 1024 * 1024)

    for segment, row_run_no in rows:
        if segment.segment_no != expected_next:
            if payload.mode == "manual":
                raise HTTPException(409, f"segment range has a gap before #{expected_next}")
            warnings.append(f"stopped_before_gap:{expected_next}")
            break
        if run_no is None:
            run_no = int(row_run_no)
        elif int(row_run_no) != run_no:
            if payload.mode == "manual":
                raise HTTPException(409, "part range cannot cross a run boundary")
            warnings.append(f"stopped_before_run_boundary:{segment.segment_no}")
            break
        if segment.storage_state != "archive_ready" or segment.integrity_state != "hashed" or not segment.sha256:
            if payload.mode == "manual":
                raise HTTPException(409, f"segment #{segment.segment_no} is not archive_ready/hashed")
            warnings.append(f"stopped_before_not_ready:{segment.segment_no}")
            break
        if segment.id in reserved_ids:
            if payload.mode == "manual":
                raise HTTPException(409, f"segment #{segment.segment_no} is already reserved by another part")
            if not selected:
                raise HTTPException(409, f"segment #{segment.segment_no} is already reserved by another part")
            warnings.append(f"stopped_before_reserved:{segment.segment_no}")
            break
        selected.append(segment)
        total_bytes += int(segment.bytes)
        total_duration += int(segment.duration_ms)
        expected_next += 1
        if payload.mode == "target" and total_bytes >= target_bytes:
            break

    if not selected:
        raise HTTPException(409, "no buildable segments are available from the requested start")
    if payload.mode == "manual" and selected[-1].segment_no != int(payload.to_segment_no):
        raise HTTPException(409, "manual range is incomplete")
    if payload.mode == "target" and total_bytes < target_bytes:
        warnings.append("target_not_reached_before_available_range_end")

    plan = {
        "mode": payload.mode,
        "from_segment_no": selected[0].segment_no,
        "end_segment_no": selected[-1].segment_no,
        "run_no": run_no,
        "segment_count": len(selected),
        "expected_bytes": total_bytes,
        "duration_ms": total_duration,
        "target_mib": payload.target_mib or settings.part_build_target_mib_default if payload.mode == "target" else None,
        "warnings": warnings,
    }
    return session, selected, int(run_no or 0), plan


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
        "deletion_group_id": str(row.deletion_group_id) if row.deletion_group_id else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "progress_percent": video_progress_percent(row),
    }


async def recorder_post(path: str, payload: dict, *, timeout: float = 30.0) -> dict:
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
    try:
        await part_builder_post("/internal/v1/capture-priority/release", {})
    except HTTPException:
        pass
    await db.refresh(row)
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_manual_stop",
            payload_json={"reason": reason},
        )
    )


def restored_video_status(row: VideoSession) -> str:
    if row.completeness_status == "complete":
        return "completed"
    if row.completeness_status == "failed":
        return "failed"
    return "completed" if row.ended_at_utc is not None else "new"


async def ensure_video_idle_for_delete(db: AsyncSession, row: VideoSession) -> None:
    if row.status in ACTIVE_VIDEO_STATUSES:
        raise HTTPException(409, "active video capture must be stopped before deleting the session")
    migration_id = await db.scalar(
        select(StorageMigrationJob.id)
        .where(
            StorageMigrationJob.status == "running",
            StorageMigrationJob.current_session_id == row.id,
        )
        .limit(1)
    )
    if migration_id is not None:
        raise HTTPException(409, "video session is currently being moved between output roots")
    active_part_job = await db.scalar(
        select(VideoPartBuildJob.id)
        .join(VideoPart, VideoPart.id == VideoPartBuildJob.part_id)
        .where(
            VideoPart.video_session_id == row.id,
            VideoPartBuildJob.status.in_(PART_ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    )
    if active_part_job is not None:
        raise HTTPException(409, "video session has an active part build")


async def quarantine_video_for_purge(row: VideoSession) -> dict:
    return await recorder_post(
        f"/internal/v1/video-sessions/{row.id}/purge-quarantine",
        {},
    )


async def restore_video_purge(ticket: dict) -> None:
    await recorder_post("/internal/v1/video-purge/restore", ticket, timeout=120.0)


async def finalize_video_purge(ticket: dict) -> None:
    await recorder_post("/internal/v1/video-purge/finalize", ticket, timeout=120.0)


async def delete_video_db_rows(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(delete(AuditLog).where(AuditLog.video_session_id == session_id))
    await db.execute(delete(VideoPart).where(VideoPart.video_session_id == session_id))
    await db.execute(delete(VideoGap).where(VideoGap.video_session_id == session_id))
    await db.execute(delete(VideoSegment).where(VideoSegment.video_session_id == session_id))
    await db.execute(delete(VideoRun).where(VideoRun.video_session_id == session_id))
    await db.execute(delete(VideoSession).where(VideoSession.id == session_id))


@router.post("/video-sessions/{session_id}/pause")
async def pause_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    if row.status == "paused":
        return video_session_dict(row)
    if row.status not in RUNNING_VIDEO_STATUSES:
        raise HTTPException(409, f"cannot pause video session in state {row.status}")

    await recorder_post(f"/internal/v1/video-sessions/{row.id}/pause", {})
    try:
        await part_builder_post("/internal/v1/capture-priority/release", {})
    except HTTPException:
        pass
    await db.refresh(row)
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_pause",
            payload_json={},
        )
    )
    await db.commit()
    await db.refresh(row)
    return video_session_dict(row)


@router.post("/video-sessions/{session_id}/resume")
async def resume_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None or row.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    if row.status in RUNNING_VIDEO_STATUSES:
        return video_session_dict(row)
    if row.status != "paused":
        raise HTTPException(409, f"cannot resume video session in state {row.status}")

    await part_builder_post("/internal/v1/capture-priority/quiesce", {})
    try:
        await recorder_post(f"/internal/v1/video-sessions/{row.id}/resume", {})
    except HTTPException:
        try:
            await part_builder_post("/internal/v1/capture-priority/release", {})
        except HTTPException:
            pass
        raise

    await db.refresh(row)
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_resume",
            payload_json={},
        )
    )
    await db.commit()
    await db.refresh(row)
    return video_session_dict(row)


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
    segment_rows = (
        await db.execute(
            select(VideoSegment, VideoRun.run_no)
            .join(VideoRun, VideoRun.id == VideoSegment.video_run_id)
            .where(VideoSegment.video_session_id == session_id, VideoSegment.segment_no > after_segment_no)
            .order_by(VideoSegment.segment_no)
            .limit(page_size + 1)
        )
    ).all()
    has_more = len(segment_rows) > page_size
    segment_rows = segment_rows[:page_size]
    return {
        "items": [
            {
                "id": segment.id,
                "segment_no": segment.segment_no,
                "video_run_id": segment.video_run_id,
                "run_no": int(run_no),
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
            for segment, run_no in segment_rows
        ],
        "next_after_segment_no": segment_rows[-1][0].segment_no if has_more and segment_rows else None,
    }


@router.get("/video-sessions")
async def list_video_sessions(
    page_size: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(VideoSession, MediaEvent)
            .join(MediaEvent, MediaEvent.id == VideoSession.event_id)
            .where(VideoSession.deleted_at_utc.is_(None))
            .order_by(VideoSession.created_at.desc())
            .limit(page_size)
        )
    ).all()
    ids = [session.id for session, _event in rows]
    aggregates: dict[uuid.UUID, tuple[int, int]] = {}
    if ids:
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
        aggregates = {session_id: (int(count or 0), int(size or 0)) for session_id, count, size in aggregate_rows}
    items = []
    for session, event in rows:
        data = video_session_dict(session)
        segment_count, total_bytes = aggregates.get(session.id, (0, 0))
        data.update({
            "segment_count": segment_count,
            "bytes": total_bytes,
            "event": {
                "id": str(event.id),
                "media_type": event.media_type,
                "channel_login": event.channel_login,
                "channel_display_name": event.channel_display_name,
                "title": event.title,
                "external_key": event.external_key,
            },
        })
        items.append(data)
    return {"items": items}


@router.post("/video-sessions/{session_id}/parts/plan")
async def plan_video_part(
    session_id: uuid.UUID,
    payload: PartPlanRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _session, _segments, _run_no, plan = await plan_part_range(db, session_id, payload)
    return plan


@router.post("/video-sessions/{session_id}/parts")
async def create_video_part(
    session_id: uuid.UUID,
    payload: PartCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await db.execute(select(VideoSession).where(VideoSession.id == session_id).with_for_update())
    session, segments, run_no, plan = await plan_part_range(db, session_id, payload, lock=True)
    part_no = int(
        await db.scalar(select(func.coalesce(func.max(VideoPart.part_no), 0)).where(VideoPart.video_session_id == session_id))
        or 0
    ) + 1
    file_name = _part_file_name(session_id, part_no, segments[0].segment_no, segments[-1].segment_no)
    metadata = session.metadata_json or {}
    output_subdir = str(metadata.get("output_subdir") or "streamhub").strip("/\\")
    relative_path = f"{output_subdir}/twitch/events/{session.event_id}/video/{session.id}/parts/{file_name}"
    active_capture = await db.scalar(
        select(VideoSession.id)
        .where(VideoSession.deleted_at_utc.is_(None), VideoSession.status.in_(ACTIVE_VIDEO_STATUSES))
        .limit(1)
    )
    queued_status = "waiting_capture_idle" if active_capture is not None else "queued"
    part = VideoPart(
        id=uuid.uuid4(),
        video_session_id=session_id,
        part_no=part_no,
        run_no=run_no,
        start_segment_no=segments[0].segment_no,
        end_segment_no=segments[-1].segment_no,
        duration_ms=plan["duration_ms"],
        expected_bytes=plan["expected_bytes"],
        file_name=file_name,
        relative_path=relative_path,
        status=queued_status,
    )
    db.add(part)
    await db.flush()
    for segment in segments:
        db.add(
            VideoPartSegment(
                part_id=part.id,
                segment_id=segment.id,
                segment_no=segment.segment_no,
                expected_bytes=segment.bytes,
            )
        )
    job = VideoPartBuildJob(
        part_id=part.id,
        status=queued_status,
        phase=queued_status,
        total_bytes=plan["expected_bytes"],
    )
    db.add(job)
    await db.commit()
    await db.refresh(part)
    await db.refresh(job)

    wakeup_error = None
    try:
        await part_builder_post(f"/internal/v1/parts/{part.id}/enqueue", {})
    except HTTPException as exc:
        wakeup_error = str(exc.detail)
    data = part_dict(part, job)
    data["plan"] = plan
    data["builder_wakeup_error"] = wakeup_error
    return data


@router.get("/video-sessions/{session_id}/parts")
async def list_video_parts(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    session = await db.get(VideoSession, session_id)
    if session is None or session.deleted_at_utc is not None:
        raise HTTPException(404, "video session not found")
    rows = (
        await db.execute(
            select(VideoPart, VideoPartBuildJob)
            .outerjoin(VideoPartBuildJob, VideoPartBuildJob.part_id == VideoPart.id)
            .where(VideoPart.video_session_id == session_id)
            .order_by(VideoPart.part_no)
        )
    ).all()
    return {"items": [part_dict(part, job) for part, job in rows]}


@router.post("/video-parts/{part_id}/retry")
async def retry_video_part(part_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    await part_builder_post(f"/internal/v1/parts/{part_id}/retry", {})
    row = (
        await db.execute(
            select(VideoPart, VideoPartBuildJob)
            .outerjoin(VideoPartBuildJob, VideoPartBuildJob.part_id == VideoPart.id)
            .where(VideoPart.id == part_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "part not found")
    return part_dict(row[0], row[1])


@router.post("/video-parts/{part_id}/cancel")
async def cancel_video_part(part_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    await part_builder_post(f"/internal/v1/parts/{part_id}/cancel", {})
    row = (
        await db.execute(
            select(VideoPart, VideoPartBuildJob)
            .outerjoin(VideoPartBuildJob, VideoPartBuildJob.part_id == VideoPart.id)
            .where(VideoPart.id == part_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "part not found")
    return part_dict(row[0], row[1])


@router.delete("/video-parts/{part_id}")
async def delete_video_part(part_id: uuid.UUID) -> dict:
    return await part_builder_delete(f"/internal/v1/parts/{part_id}", timeout=120.0)


@router.get("/deleted/video-sessions")
async def list_deleted_video_sessions(
    page_size: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(VideoSession)
            .where(VideoSession.deleted_at_utc.is_not(None))
            .order_by(VideoSession.deleted_at_utc.desc(), VideoSession.created_at.desc())
            .limit(page_size)
        )
    ).scalars().all()
    ids = [row.id for row in rows]
    aggregates = {}
    if ids:
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
        aggregates = {
            session_id: (int(count or 0), int(total_bytes or 0))
            for session_id, count, total_bytes in aggregate_rows
        }
    items = []
    for row in rows:
        segment_count, total_bytes = aggregates.get(row.id, (0, 0))
        data = video_session_dict(row)
        data["segment_count"] = segment_count
        data["bytes"] = total_bytes
        items.append(data)
    return {"items": items}


@router.delete("/video-sessions/{session_id}")
async def soft_delete_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None:
        raise HTTPException(404, "video session not found")
    if row.deleted_at_utc is not None:
        return {"ok": True, "already_deleted": True}
    await ensure_video_idle_for_delete(db, row)
    row.deleted_at_utc = datetime.now(UTC).replace(tzinfo=None)
    row.deletion_group_id = None
    row.status = "soft_deleted"
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_soft_delete",
            payload_json={},
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/video-sessions/{session_id}/restore")
async def restore_video_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(VideoSession, session_id)
    if row is None:
        raise HTTPException(404, "video session not found")
    if row.deleted_at_utc is None:
        return {"ok": True, "already_restored": True}
    row.deleted_at_utc = None
    row.deletion_group_id = None
    row.status = restored_video_status(row)
    db.add(
        AuditLog(
            event_id=row.event_id,
            video_session_id=row.id,
            action="video_restore",
            payload_json={},
        )
    )
    await db.commit()
    return {"ok": True}


@router.delete("/deleted/video-sessions/{session_id}")
async def purge_video_session(
    session_id: uuid.UUID,
    permanent: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not permanent:
        raise HTTPException(400, "permanent=true and explicit UI confirmation are required")
    row = await db.get(VideoSession, session_id)
    if row is None:
        raise HTTPException(404, "video session not found")
    if row.deleted_at_utc is None:
        raise HTTPException(409, "video session must be soft-deleted first")
    await ensure_video_idle_for_delete(db, row)
    ticket = await quarantine_video_for_purge(row)
    try:
        await delete_video_db_rows(db, session_id)
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await restore_video_purge(ticket)
        except Exception:
            pass
        raise

    cleanup_pending = False
    try:
        await finalize_video_purge(ticket)
    except HTTPException:
        cleanup_pending = True
    return {
        "ok": True,
        "purged": True,
        "filesystem_cleanup_pending": cleanup_pending,
    }
