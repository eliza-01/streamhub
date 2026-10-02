from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import (
    StorageMigrationJob,
    StorageOutputSetting,
    VideoPart,
    VideoPartBuildJob,
    VideoSegment,
    VideoSession,
)
from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
settings = get_settings()

ACTIVE_VIDEO_STATUSES = frozenset({"arming", "recording", "reconnecting"})
ACTIVE_MIGRATION_STATUSES = frozenset({"queued", "running"})
PART_ACTIVE_JOB_STATUSES = frozenset({"queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"})


class OutputSettingsUpdate(BaseModel):
    output_root_key: str
    output_subdir: str
    batch_segments: int = Field(default=100, ge=1, le=1000)


class StorageMigrationCreate(BaseModel):
    source_root_key: str
    destination_root_key: str


def available_output_roots() -> list[dict[str, str]]:
    roots = [
        {"key": "root1", "label": settings.video_output_root_1_label},
    ]
    if settings.video_output_root_2_enabled:
        roots.append({"key": "root2", "label": settings.video_output_root_2_label})
    if settings.video_output_root_3_enabled:
        roots.append({"key": "root3", "label": settings.video_output_root_3_label})
    return roots


def normalize_output_subdir(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw:
        raise HTTPException(400, "output subdirectory is required")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(400, "output subdirectory must be a safe relative path")
    if any(":" in part for part in path.parts):
        raise HTTPException(400, "drive letters belong to the configured output root, not the subdirectory")
    return path.as_posix()


async def get_or_create_output_setting(db: AsyncSession) -> StorageOutputSetting:
    row = await db.get(StorageOutputSetting, 1)
    if row is None:
        row = StorageOutputSetting(
            id=1,
            output_root_key="root1",
            output_subdir="streamhub",
            batch_segments=settings.video_archive_batch_segments,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def payload(row: StorageOutputSetting) -> dict:
    roots = available_output_roots()
    return {
        "output_root_key": row.output_root_key,
        "output_subdir": row.output_subdir,
        "roots": roots,
        "batch_segments": int(row.batch_segments or settings.video_archive_batch_segments),
        "applies_to": "new_video_sessions",
    }


def migration_payload(row: StorageMigrationJob) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "source_root_key": row.source_root_key,
        "destination_root_key": row.destination_root_key,
        "total_sessions": int(row.total_sessions or 0),
        "migrated_sessions": int(row.migrated_sessions or 0),
        "skipped_sessions": int(row.skipped_sessions or 0),
        "total_bytes": int(row.total_bytes or 0),
        "copied_bytes": int(row.copied_bytes or 0),
        "current_session_id": str(row.current_session_id) if row.current_session_id else None,
        "last_error": row.last_error,
        "created_at": row.created_at,
        "started_at_utc": row.started_at_utc,
        "completed_at_utc": row.completed_at_utc,
        "updated_at": row.updated_at,
    }


@router.get("/output-settings")
async def read_output_settings(db: AsyncSession = Depends(get_db)) -> dict:
    return payload(await get_or_create_output_setting(db))


@router.put("/output-settings")
async def update_output_settings(body: OutputSettingsUpdate, db: AsyncSession = Depends(get_db)) -> dict:
    roots = {item["key"] for item in available_output_roots()}
    if body.output_root_key not in roots:
        raise HTTPException(400, "output root is not enabled")
    row = await get_or_create_output_setting(db)
    row.output_root_key = body.output_root_key
    row.output_subdir = normalize_output_subdir(body.output_subdir)
    row.batch_segments = body.batch_segments
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(row)
    return payload(row)


@router.get("/migrations")
async def list_storage_migrations(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(StorageMigrationJob).order_by(StorageMigrationJob.id.desc()).limit(limit)
        )
    ).scalars().all()
    return {"items": [migration_payload(row) for row in rows]}


@router.post("/migrations")
async def create_storage_migration(
    body: StorageMigrationCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    roots = {item["key"] for item in available_output_roots()}
    if body.source_root_key not in roots or body.destination_root_key not in roots:
        raise HTTPException(400, "source and destination output roots must both be enabled")
    if body.source_root_key == body.destination_root_key:
        raise HTTPException(400, "source and destination output roots must be different")

    active_job = await db.scalar(
        select(StorageMigrationJob.id)
        .where(StorageMigrationJob.status.in_(ACTIVE_MIGRATION_STATUSES))
        .limit(1)
    )
    if active_job is not None:
        raise HTTPException(409, "another storage migration is already queued or running")

    sessions = (
        await db.execute(select(VideoSession).order_by(VideoSession.created_at, VideoSession.id))
    ).scalars().all()
    source_sessions = [
        row
        for row in sessions
        if str((row.metadata_json or {}).get("output_root_key") or "root1") == body.source_root_key
    ]
    source_ids = [row.id for row in source_sessions]

    stats: dict = {}
    if source_ids:
        stats_rows = (
            await db.execute(
                select(
                    VideoSegment.video_session_id,
                    func.count(VideoSegment.id),
                    func.coalesce(func.sum(VideoSegment.bytes), 0),
                    func.coalesce(
                        func.sum(case((VideoSegment.storage_state != "archive_ready", 1), else_=0)),
                        0,
                    ),
                )
                .where(VideoSegment.video_session_id.in_(source_ids))
                .group_by(VideoSegment.video_session_id)
            )
        ).all()
        stats = {
            session_id: (int(count or 0), int(total_bytes or 0), int(non_ready or 0))
            for session_id, count, total_bytes, non_ready in stats_rows
        }

    ready_part_bytes: dict = {}
    sessions_with_active_part_jobs: set = set()
    if source_ids:
        part_rows = (
            await db.execute(
                select(
                    VideoPart.video_session_id,
                    func.coalesce(func.sum(VideoPart.final_bytes), 0),
                )
                .where(
                    VideoPart.video_session_id.in_(source_ids),
                    VideoPart.status == "ready",
                )
                .group_by(VideoPart.video_session_id)
            )
        ).all()
        ready_part_bytes = {session_id: int(total or 0) for session_id, total in part_rows}
        sessions_with_active_part_jobs = set(
            (
                await db.execute(
                    select(VideoPart.video_session_id)
                    .join(VideoPartBuildJob, VideoPartBuildJob.part_id == VideoPart.id)
                    .where(
                        VideoPart.video_session_id.in_(source_ids),
                        VideoPartBuildJob.status.in_(PART_ACTIVE_JOB_STATUSES),
                    )
                    .distinct()
                )
            ).scalars().all()
        )

    eligible: list[VideoSession] = []
    skipped = 0
    total_bytes = 0
    for row in source_sessions:
        _segment_count, segment_bytes, non_ready = stats.get(row.id, (0, 0, 0))
        if (
            row.status in ACTIVE_VIDEO_STATUSES
            or non_ready > 0
            or row.id in sessions_with_active_part_jobs
        ):
            skipped += 1
            continue
        eligible.append(row)
        total_bytes += segment_bytes + ready_part_bytes.get(row.id, 0)

    if not eligible:
        raise HTTPException(
            409,
            "no idle video sessions with fully archived segments and no pending part builds were found on the source root",
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    job = StorageMigrationJob(
        status="queued",
        source_root_key=body.source_root_key,
        destination_root_key=body.destination_root_key,
        session_ids_json=[str(row.id) for row in eligible],
        total_sessions=len(eligible),
        migrated_sessions=0,
        skipped_sessions=skipped,
        total_bytes=total_bytes,
        copied_bytes=0,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return migration_payload(job)


@router.post("/migrations/{job_id}/retry")
async def retry_storage_migration(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await db.get(StorageMigrationJob, job_id)
    if job is None:
        raise HTTPException(404, "storage migration not found")
    if job.status != "failed":
        raise HTTPException(409, "only failed storage migrations can be retried")
    another = await db.scalar(
        select(StorageMigrationJob.id)
        .where(
            StorageMigrationJob.id != job_id,
            StorageMigrationJob.status.in_(ACTIVE_MIGRATION_STATUSES),
        )
        .limit(1)
    )
    if another is not None:
        raise HTTPException(409, "another storage migration is already queued or running")
    job.status = "queued"
    job.migrated_sessions = 0
    job.copied_bytes = 0
    job.current_session_id = None
    job.last_error = None
    job.started_at_utc = None
    job.completed_at_utc = None
    job.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    return migration_payload(job)
