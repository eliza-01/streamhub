from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update

from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import (
    MediaEvent,
    StorageMigrationJob,
    StorageOutputSetting,
    VideoGap,
    VideoPart,
    VideoPartBuildJob,
    VideoRun,
    VideoSegment,
    VideoSession,
)
from streamhub_common.security import require_internal_token
from streamhub_common.settings import get_settings

from app.commands import build_ffmpeg_command, build_streamlink_command, segment_no_from_name
from app.storage import (
    ArchiveCopyError,
    archive_segment_relative_path,
    atomic_copy_tree_verified,
    atomic_copy_verified,
    atomic_write_text,
    delete_quarantined_directory,
    directory_is_writable,
    find_purge_quarantines,
    ensure_archive_dirs,
    restore_quarantined_directory,
    quarantine_directory,
    safe_archive_session_root,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("streamhub.video_recorder")

ACTIVE_VIDEO_STATUSES = frozenset({"arming", "recording", "reconnecting"})


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def safe_session_root(session_id: uuid.UUID) -> Path:
    root = Path(settings.video_spool_root).resolve()
    candidate = (root / str(session_id)).resolve()
    candidate.relative_to(root)
    return candidate




def enabled_output_roots() -> dict[str, Path]:
    roots = {"root1": Path(settings.video_output_root_1)}
    if settings.video_output_root_2_enabled:
        roots["root2"] = Path(settings.video_output_root_2)
    if settings.video_output_root_3_enabled:
        roots["root3"] = Path(settings.video_output_root_3)
    return roots


def configured_output_root(key: str) -> Path:
    roots = {
        "root1": Path(settings.video_output_root_1),
        "root2": Path(settings.video_output_root_2),
        "root3": Path(settings.video_output_root_3),
    }
    root = roots.get(key)
    if root is None:
        raise ValueError(f"unknown video output root: {key}")
    return root


def normalize_output_subdir(value: str | None) -> PurePosixPath:
    raw = (value or "streamhub").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise ValueError(f"unsafe output subdirectory: {value!r}")
    return path


def session_output_base(metadata: dict | None) -> Path:
    metadata = metadata or {}
    key = str(metadata.get("output_root_key") or "root1")
    roots = enabled_output_roots()
    root = roots.get(key)
    if root is None:
        raise ValueError(f"video output root is not enabled: {key}")
    root = root.resolve()
    subdir = normalize_output_subdir(metadata.get("output_subdir"))
    candidate = (root / Path(*subdir.parts)).resolve()
    candidate.relative_to(root)
    return candidate

def ensure_spool_dirs(session_id: uuid.UUID) -> tuple[Path, Path, Path]:
    root = safe_session_root(session_id)
    segments = root / "segments"
    runs = root / "runs"
    logs = root / "logs"
    for path in (segments, runs, logs):
        path.mkdir(parents=True, exist_ok=True)
    return segments, runs, logs


class VideoStartRequest(BaseModel):
    session_id: uuid.UUID
    event_id: uuid.UUID
    media_type: Literal["live", "vod"]
    source_url: str
    quality: str = "best"
    source_duration_ms: int | None = None


class StopRequest(BaseModel):
    reason: str = "user_stop"


class PurgeTicket(BaseModel):
    token: str
    session_id: uuid.UUID
    event_id: uuid.UUID
    output_root_key: str
    output_subdir: str
    archive_quarantined: bool = False
    spool_quarantined: bool = False


async def probe_duration_ms(path: Path) -> int:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {stderr.decode(errors='replace')[:300]}")
    try:
        seconds = float(stdout.decode().strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned invalid duration for {path.name}") from exc
    return max(1, int(round(seconds * 1000)))


async def max_segment_no(session_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        value = await db.scalar(
            select(func.max(VideoSegment.segment_no)).where(VideoSegment.video_session_id == session_id)
        )
        session = await db.get(VideoSession, session_id)
    db_max = int(value or 0)
    segments_dir, _runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    spool_max = 0
    for path in segments_dir.glob("seg_*.ts*"):
        number = segment_no_from_name(path.name)
        if number is not None:
            spool_max = max(spool_max, number)

    archive_max = 0
    if session is not None:
        archive_dir = safe_archive_session_root(
            session_output_base(session.metadata_json), session.event_id, session_id
        ) / "segments"
        if archive_dir.exists():
            for path in archive_dir.glob("seg_*.ts"):
                number = segment_no_from_name(path.name)
                if number is not None:
                    archive_max = max(archive_max, number)
    return max(db_max, spool_max, archive_max)


async def next_run_no(session_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        value = await db.scalar(select(func.max(VideoRun.run_no)).where(VideoRun.video_session_id == session_id))
        return int(value or 0) + 1


async def build_spool_playlist(session_id: uuid.UUID, *, finished: bool = False) -> None:
    root = safe_session_root(session_id)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(VideoSegment, VideoRun.run_no)
                .join(VideoRun, VideoRun.id == VideoSegment.video_run_id)
                .where(VideoSegment.video_session_id == session_id)
                .order_by(VideoSegment.segment_no)
            )
        ).all()
    if not rows:
        return

    target_duration = max(1, max((segment.duration_ms + 999) // 1000 for segment, _run_no in rows))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target_duration}", "#EXT-X-MEDIA-SEQUENCE:0"]
    previous_run = None
    for segment, run_no in rows:
        if previous_run is not None and run_no != previous_run:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{segment.duration_ms / 1000:.3f},")
        lines.append(f"segments/{segment.file_name}")
        previous_run = run_no
    if finished:
        lines.append("#EXT-X-ENDLIST")

    tmp = root / "recording.m3u8.tmp"
    final = root / "recording.m3u8"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, final)


def parse_hls_program_date_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def playlist_segment_program_times(session_id: uuid.UUID, run_no: int) -> dict[int, datetime | None]:
    """Return closed segment numbers and their wall-clock HLS PROGRAM-DATE-TIME.

    ffmpeg writes PROGRAM-DATE-TIME for LIVE captures. Keep carrying the last
    timestamp forward by EXTINF duration as a defensive fallback in case a
    muxer emits the tag only at a discontinuity boundary.
    """
    _segments_dir, runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    playlist = runs_dir / f"run_{run_no:06d}.m3u8"
    if not playlist.exists():
        return {}
    try:
        lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    result: dict[int, datetime | None] = {}
    program_time: datetime | None = None
    duration_ms: int | None = None
    for line in lines:
        value = line.strip()
        if not value:
            continue
        if value.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            program_time = parse_hls_program_date_time(value.split(":", 1)[1])
            continue
        if value.startswith("#EXTINF:"):
            raw = value.split(":", 1)[1].split(",", 1)[0].strip()
            try:
                duration_ms = max(0, int(round(float(raw) * 1000)))
            except ValueError:
                duration_ms = None
            continue
        if value.startswith("#"):
            continue

        name = Path(value).name
        if not name.startswith("seg_") or not name.endswith(".ts"):
            continue
        try:
            segment_no = int(name[4:-3])
        except ValueError:
            continue
        result[segment_no] = program_time
        if program_time is not None and duration_ms is not None:
            program_time = program_time + timedelta(milliseconds=duration_ms)
        duration_ms = None
    return result


def utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def source_offset_ms(value: datetime | None, origin: datetime | None) -> int | None:
    value_naive = utc_naive(value)
    origin_naive = utc_naive(origin)
    if value_naive is None or origin_naive is None:
        return None
    return max(0, int(round((value_naive - origin_naive).total_seconds() * 1000)))


async def index_closed_segments(session_id: uuid.UUID, run_id: int, run_no: int) -> int:
    segments_dir, _runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    segment_program_times = playlist_segment_program_times(session_id, run_no)
    allowed_segment_numbers = set(segment_program_times)
    if not allowed_segment_numbers:
        return 0
    async with SessionLocal() as db:
        existing_rows = (
            await db.execute(
                select(VideoSegment).where(VideoSegment.video_session_id == session_id)
            )
        ).scalars().all()
        existing = {row.segment_no: row for row in existing_rows}
        session = await db.get(VideoSession, session_id)
        run = await db.get(VideoRun, run_id)
        if session is None or run is None:
            return 0
        event = await db.get(MediaEvent, session.event_id)

        media_type = str((session.metadata_json or {}).get("media_type") or "")
        source_origin = event.source_started_at_utc if event is not None else None
        run_rows = [row for row in existing_rows if row.video_run_id == run_id]
        timeline_cursor = int(session.coverage_end_ms or 0)
        run_timeline_start = min((int(row.timeline_start_ms) for row in run_rows), default=timeline_cursor)
        run_source_start = source_offset_ms(run.started_at_utc, source_origin) if media_type == "live" else None

        def mapped_source_start(segment_no: int, timeline_start_ms: int) -> int | None:
            if media_type == "vod":
                return timeline_start_ms
            if media_type != "live" or source_origin is None:
                return None
            program_time = segment_program_times.get(segment_no)
            exact = source_offset_ms(program_time, source_origin)
            if exact is not None:
                return exact
            if run_source_start is None:
                return None
            return max(0, run_source_start + timeline_start_ms - run_timeline_start)

        updated_source = 0
        # Reconciliation may revisit already indexed rows. Backfill/upgrade their
        # source timeline from PROGRAM-DATE-TIME instead of leaving a historical
        # LIVE capture permanently unsynchronised.
        for segment_no in sorted(allowed_segment_numbers):
            row = existing.get(segment_no)
            if row is None or row.video_run_id != run_id:
                continue
            source_start = mapped_source_start(segment_no, int(row.timeline_start_ms))
            if source_start is None:
                continue
            source_end = source_start + int(row.duration_ms)
            if row.source_media_start_ms != source_start or row.source_media_end_ms != source_end:
                row.source_media_start_ms = source_start
                row.source_media_end_ms = source_end
                updated_source += 1

        created = 0
        for path in sorted(segments_dir.glob("seg_*.ts")):
            try:
                segment_no = int(path.stem.split("_")[-1])
            except ValueError:
                continue
            if segment_no not in allowed_segment_numbers:
                continue
            if segment_no in existing or path.with_suffix(path.suffix + ".tmp").exists():
                continue
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size <= 0:
                continue
            duration_ms = await probe_duration_ms(path)
            source_start = mapped_source_start(segment_no, timeline_cursor)
            source_end = source_start + duration_ms if source_start is not None else None
            row = VideoSegment(
                video_session_id=session_id,
                video_run_id=run_id,
                segment_no=segment_no,
                file_name=path.name,
                relative_path=f"segments/{path.name}",
                timeline_start_ms=timeline_cursor,
                timeline_end_ms=timeline_cursor + duration_ms,
                source_media_start_ms=source_start,
                source_media_end_ms=source_end,
                duration_ms=duration_ms,
                bytes=size,
                storage_state="spool",
                integrity_state="size_verified",
                closed_at_utc=utcnow_naive(),
            )
            db.add(row)
            timeline_cursor += duration_ms
            existing[segment_no] = row
            created += 1
            run.first_segment_no = segment_no if run.first_segment_no is None else min(run.first_segment_no, segment_no)
            run.last_segment_no = segment_no if run.last_segment_no is None else max(run.last_segment_no, segment_no)

        if created:
            session.duration_recorded_ms = timeline_cursor
            session.coverage_start_ms = 0 if session.coverage_start_ms is None else session.coverage_start_ms
            session.coverage_end_ms = timeline_cursor
            session.last_activity_at_utc = utcnow_naive()
        if created or updated_source:
            await db.commit()
        else:
            await db.rollback()

    if created:
        await build_spool_playlist(session_id)
        archive_wakeup.set()
    return created


@dataclass(frozen=True)
class ArchiveCandidate:
    segment_id: int
    session_id: uuid.UUID
    event_id: uuid.UUID
    file_name: str
    expected_bytes: int
    output_root_key: str
    output_subdir: str


archive_wakeup = asyncio.Event()
archive_shutdown = asyncio.Event()
archive_metadata_lock = asyncio.Lock()


@dataclass
class StorageWorkerStatus:
    worker_no: int
    state: str = "starting"
    heartbeat_at_utc: datetime | None = None
    last_success_at_utc: datetime | None = None
    last_error_at_utc: datetime | None = None
    last_error: str | None = None
    claim_failures: int = 0
    batch_failures: int = 0
    batches_completed: int = 0
    segments_completed: int = 0
    current_session_id: uuid.UUID | None = None
    current_batch_size: int = 0


storage_worker_status: dict[int, StorageWorkerStatus] = {}
storage_worker_tasks: dict[int, asyncio.Task] = {}


def touch_storage_worker(worker_no: int, *, state: str | None = None) -> StorageWorkerStatus:
    status = storage_worker_status.setdefault(worker_no, StorageWorkerStatus(worker_no=worker_no))
    status.heartbeat_at_utc = utcnow_naive()
    if state is not None:
        status.state = state
    return status


def storage_worker_status_payload() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for worker_no in range(1, settings.video_storage_copy_workers + 1):
        status = storage_worker_status.get(worker_no)
        task = storage_worker_tasks.get(worker_no)
        if status is None:
            result[str(worker_no)] = {
                "state": "missing",
                "task_alive": bool(task is not None and not task.done()),
            }
            continue
        result[str(worker_no)] = {
            "state": status.state,
            "task_alive": bool(task is not None and not task.done()),
            "heartbeat_at_utc": iso_utc(status.heartbeat_at_utc),
            "last_success_at_utc": iso_utc(status.last_success_at_utc),
            "last_error_at_utc": iso_utc(status.last_error_at_utc),
            "last_error": status.last_error,
            "claim_failures": status.claim_failures,
            "batch_failures": status.batch_failures,
            "batches_completed": status.batches_completed,
            "segments_completed": status.segments_completed,
            "current_session_id": str(status.current_session_id) if status.current_session_id else None,
            "current_batch_size": status.current_batch_size,
        }
    return result


def archive_source_path(candidate: ArchiveCandidate) -> Path:
    return safe_session_root(candidate.session_id) / "segments" / candidate.file_name


def candidate_output_base(candidate: ArchiveCandidate) -> Path:
    return session_output_base({
        "output_root_key": candidate.output_root_key,
        "output_subdir": candidate.output_subdir,
    })


def archive_final_path(candidate: ArchiveCandidate) -> Path:
    segments_dir, _runs_dir, _parts_dir, _logs_dir = ensure_archive_dirs(
        candidate_output_base(candidate), candidate.event_id, candidate.session_id
    )
    return segments_dir / candidate.file_name


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def claim_archive_batch() -> list[ArchiveCandidate]:
    terminal_statuses = {"completed", "failed", "soft_deleted", "paused"}
    async with SessionLocal() as db:
        output_setting = await db.get(StorageOutputSetting, 1)
        batch_size = int(
            output_setting.batch_segments if output_setting is not None else settings.video_archive_batch_segments
        )
        grouped = (
            await db.execute(
                select(
                    VideoSession.id,
                    VideoSession.event_id,
                    VideoSession.status,
                    func.count(VideoSegment.id).label("spool_count"),
                    func.min(VideoSegment.closed_at_utc).label("oldest_closed"),
                )
                .join(VideoSegment, VideoSegment.video_session_id == VideoSession.id)
                .where(
                    VideoSegment.storage_state == "spool",
                    VideoSession.deleted_at_utc.is_(None),
                )
                .group_by(
                    VideoSession.id,
                    VideoSession.event_id,
                    VideoSession.status,
                )
                .order_by(func.min(VideoSegment.closed_at_utc), VideoSession.id)
                .limit(50)
            )
        ).all()

        selected = None
        for row in grouped:
            if int(row.spool_count or 0) >= batch_size or row.status in terminal_statuses:
                selected = row
                break
        if selected is None:
            await db.rollback()
            return []

        selected_session = await db.get(VideoSession, selected.id)
        if selected_session is None:
            await db.rollback()
            return []
        metadata = selected_session.metadata_json or {}
        output_root_key = str(metadata.get("output_root_key") or "root1")
        output_subdir = str(metadata.get("output_subdir") or "streamhub")
        segments = (
            await db.execute(
                select(VideoSegment)
                .where(
                    VideoSegment.video_session_id == selected.id,
                    VideoSegment.storage_state == "spool",
                )
                .order_by(VideoSegment.segment_no)
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        ).scalars().all()
        if not segments:
            await db.rollback()
            return []

        candidates: list[ArchiveCandidate] = []
        for segment in segments:
            segment.storage_state = "copying"
            segment.archive_attempts = int(segment.archive_attempts or 0) + 1
            segment.archive_last_error = None
            candidates.append(
                ArchiveCandidate(
                    segment_id=segment.id,
                    session_id=segment.video_session_id,
                    event_id=selected.event_id,
                    file_name=segment.file_name,
                    expected_bytes=int(segment.bytes),
                    output_root_key=output_root_key,
                    output_subdir=output_subdir,
                )
            )
        await db.commit()
        return candidates


async def mark_archive_failure(candidate: ArchiveCandidate, error: Exception) -> None:
    source = archive_source_path(candidate)
    final = archive_final_path(candidate)
    async with SessionLocal() as db:
        row = await db.get(VideoSegment, candidate.segment_id)
        if row is None or row.storage_state == "archive_ready":
            return
        if not source.exists() and not final.exists():
            row.storage_state = "missing"
            row.integrity_state = "failed"
        else:
            row.storage_state = "spool"
        row.archive_last_error = str(error)[:4000]
        await db.commit()
    logger.warning(
        "archive handoff failed session=%s segment=%s error=%s",
        candidate.session_id,
        candidate.file_name,
        error,
    )


def cleanup_spool_copy_after_commit(candidate: ArchiveCandidate) -> None:
    source = archive_source_path(candidate)
    final = archive_final_path(candidate)
    if not source.exists():
        return
    if not final.exists() or final.stat().st_size != candidate.expected_bytes:
        raise ArchiveCopyError(f"archive final missing during spool cleanup: {candidate.file_name}")
    if source.stat().st_size != candidate.expected_bytes:
        raise ArchiveCopyError(f"spool size changed before cleanup: {candidate.file_name}")
    source.unlink()


async def archive_rows_for_session(session_id: uuid.UUID):
    async with SessionLocal() as db:
        session = await db.get(VideoSession, session_id)
        if session is None:
            return None, [], []
        runs = (
            await db.execute(
                select(VideoRun).where(VideoRun.video_session_id == session_id).order_by(VideoRun.run_no)
            )
        ).scalars().all()
        rows = (
            await db.execute(
                select(VideoSegment, VideoRun.run_no)
                .join(VideoRun, VideoRun.id == VideoSegment.video_run_id)
                .where(VideoSegment.video_session_id == session_id)
                .order_by(VideoSegment.segment_no)
            )
        ).all()
        return session, runs, rows


def playlist_text(rows: list[tuple[VideoSegment, int]], *, finished: bool, run_relative: bool = False) -> str:
    if not rows:
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        if finished:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"
    target_duration = max(1, max((segment.duration_ms + 999) // 1000 for segment, _run_no in rows))
    first_segment = rows[0][0].segment_no
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{first_segment}",
    ]
    previous_run = None
    for segment, run_no in rows:
        if previous_run is not None and run_no != previous_run:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{segment.duration_ms / 1000:.3f},")
        prefix = "../segments" if run_relative else "segments"
        lines.append(f"{prefix}/{segment.file_name}")
        previous_run = run_no
    if finished:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


async def refresh_archive_metadata(session_id: uuid.UUID) -> None:
    async with archive_metadata_lock:
        session, runs, rows = await archive_rows_for_session(session_id)
        if session is None:
            return
        output_base = session_output_base(session.metadata_json)
        root = safe_archive_session_root(output_base, session.event_id, session.id)
        _segments_dir, runs_dir, _parts_dir, _logs_dir = ensure_archive_dirs(
            output_base, session.event_id, session.id
        )
        ready_rows = [(segment, run_no) for segment, run_no in rows if segment.storage_state == "archive_ready"]
        terminal = session.status in {"completed", "failed", "soft_deleted"}
        all_ready = len(ready_rows) == len(rows)
        atomic_write_text(root / "recording.m3u8", playlist_text(ready_rows, finished=terminal and all_ready))

        run_by_no = {run.run_no: run for run in runs}
        for run_no, run in run_by_no.items():
            all_for_run = [(segment, n) for segment, n in rows if n == run_no]
            ready_for_run = [(segment, n) for segment, n in ready_rows if n == run_no]
            run_finished = run.status not in {"starting", "running"} and len(all_for_run) == len(ready_for_run)
            atomic_write_text(
                runs_dir / f"run_{run_no:06d}.m3u8",
                playlist_text(ready_for_run, finished=run_finished, run_relative=True),
            )

        state_counts: dict[str, int] = {}
        state_bytes: dict[str, int] = {}
        for segment, _run_no in rows:
            state_counts[segment.storage_state] = state_counts.get(segment.storage_state, 0) + 1
            state_bytes[segment.storage_state] = state_bytes.get(segment.storage_state, 0) + int(segment.bytes)

        metadata = session.metadata_json or {}
        manifest = {
            "schema_version": 1,
            "event_id": str(session.event_id),
            "video_session_id": str(session.id),
            "media_type": metadata.get("media_type"),
            "source_ids": {
                "video_external_id": metadata.get("video_external_id"),
                "stream_external_id": metadata.get("stream_external_id"),
                "channel_login": metadata.get("channel_login"),
            },
            "quality": session.quality,
            "recorder_mode": session.recorder_mode,
            "status": session.status,
            "completeness_status": session.completeness_status,
            "recording_started_at_utc": iso_utc(session.recording_started_at_utc),
            "ended_at_utc": iso_utc(session.ended_at_utc),
            "required_start_ms": session.required_start_ms,
            "required_end_ms": session.required_end_ms,
            "coverage_start_ms": session.coverage_start_ms,
            "coverage_end_ms": session.coverage_end_ms,
            "duration_recorded_ms": session.duration_recorded_ms,
            "segment_count": len(rows),
            "archive_ready_count": len(ready_rows),
            "storage_counts": state_counts,
            "storage_bytes": state_bytes,
            "runs": [
                {
                    "run_no": run.run_no,
                    "status": run.status,
                    "started_at_utc": iso_utc(run.started_at_utc),
                    "ended_at_utc": iso_utc(run.ended_at_utc),
                    "resume_source_offset_ms": run.resume_source_offset_ms,
                    "first_segment_no": run.first_segment_no,
                    "last_segment_no": run.last_segment_no,
                    "close_reason": run.close_reason,
                }
                for run in runs
            ],
            "updated_at_utc": iso_utc(utcnow_naive()),
        }
        atomic_write_text(root / "session.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


async def maybe_cleanup_terminal_spool(session_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        session = await db.get(VideoSession, session_id)
        if session is None or session.status not in {"completed", "failed", "soft_deleted"}:
            return
        pending = await db.scalar(
            select(func.count(VideoSegment.id)).where(
                VideoSegment.video_session_id == session_id,
                VideoSegment.storage_state != "archive_ready",
            )
        )
        event_id = session.event_id
    if int(pending or 0) != 0:
        return

    await refresh_archive_metadata(session_id)
    spool_root = safe_session_root(session_id)
    source_log = spool_root / "logs" / "recorder.log"
    if source_log.exists():
        output_base = session_output_base(session.metadata_json)
        _segments, _runs, _parts, archive_logs = ensure_archive_dirs(
            output_base, event_id, session_id
        )
        try:
            await asyncio.to_thread(
                atomic_copy_verified,
                source_log,
                archive_logs / "recorder.log",
                expected_bytes=source_log.stat().st_size,
                min_free_bytes=0,
            )
        except Exception:
            logger.exception("failed archiving recorder log session=%s", session_id)
            return
    try:
        await asyncio.to_thread(shutil.rmtree, spool_root)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("failed removing completed spool session=%s", session_id)


async def process_archive_batch(candidates: list[ArchiveCandidate]) -> None:
    if not candidates:
        return
    copied: dict[int, str] = {}
    for candidate in candidates:
        result = await asyncio.to_thread(
            atomic_copy_verified,
            archive_source_path(candidate),
            archive_final_path(candidate),
            expected_bytes=candidate.expected_bytes,
            min_free_bytes=settings.video_archive_min_free_bytes,
        )
        copied[candidate.segment_id] = result.sha256

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(VideoSegment).where(VideoSegment.id.in_([candidate.segment_id for candidate in candidates]))
            )
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        now = utcnow_naive()
        for candidate in candidates:
            row = by_id.get(candidate.segment_id)
            if row is None:
                raise RuntimeError(f"video segment disappeared during archive batch: {candidate.segment_id}")
            row.storage_state = "archive_ready"
            row.integrity_state = "hashed"
            row.sha256 = copied[candidate.segment_id]
            row.relative_path = str(
                PurePosixPath(candidate.output_subdir)
                / archive_segment_relative_path(candidate.event_id, candidate.session_id, candidate.file_name)
            )
            row.archived_at_utc = now
            row.archive_last_error = None
        await db.commit()

    # One DB commit releases the whole batch. Only after every copied segment is
    # durable and every row is archive_ready do we reclaim spool space.
    for candidate in candidates:
        try:
            await asyncio.to_thread(cleanup_spool_copy_after_commit, candidate)
        except Exception:
            logger.exception(
                "archive batch committed but spool cleanup is pending session=%s segment=%s",
                candidate.session_id,
                candidate.file_name,
            )

    session_id = candidates[0].session_id
    try:
        await refresh_archive_metadata(session_id)
        await maybe_cleanup_terminal_spool(session_id)
    except Exception:
        logger.exception("failed refreshing archive metadata session=%s", session_id)


async def storage_worker_retry_delay() -> None:
    try:
        await asyncio.wait_for(archive_shutdown.wait(), timeout=settings.video_archive_retry_seconds)
    except asyncio.TimeoutError:
        pass


async def storage_worker_loop(worker_no: int) -> None:
    status = touch_storage_worker(worker_no, state="starting")
    try:
        while not archive_shutdown.is_set():
            candidates: list[ArchiveCandidate] = []
            try:
                archive_wakeup.clear()
                status = touch_storage_worker(worker_no, state="claiming")
                candidates = await claim_archive_batch()
                if not candidates:
                    status.current_session_id = None
                    status.current_batch_size = 0
                    touch_storage_worker(worker_no, state="idle")
                    try:
                        await asyncio.wait_for(archive_wakeup.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    continue

                status.current_session_id = candidates[0].session_id
                status.current_batch_size = len(candidates)
                touch_storage_worker(worker_no, state="copying")
                await process_archive_batch(candidates)
                status.batches_completed += 1
                status.segments_completed += len(candidates)
                status.last_success_at_utc = utcnow_naive()
                status.current_session_id = None
                status.current_batch_size = 0
                touch_storage_worker(worker_no, state="idle")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = utcnow_naive()
                status.last_error_at_utc = now
                status.last_error = str(exc)[:4000]
                if candidates:
                    status.batch_failures += 1
                    logger.exception(
                        "storage worker batch failed worker=%s session=%s batch=%s; retrying",
                        worker_no,
                        candidates[0].session_id,
                        len(candidates),
                    )
                    for candidate in candidates:
                        try:
                            await mark_archive_failure(candidate, exc)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception(
                                "storage worker could not reset failed archive candidate worker=%s "
                                "session=%s segment=%s",
                                worker_no,
                                candidate.session_id,
                                candidate.file_name,
                            )
                else:
                    status.claim_failures += 1
                    logger.exception(
                        "storage worker claim failed worker=%s; retrying instead of stopping worker",
                        worker_no,
                    )
                status.current_session_id = None
                status.current_batch_size = 0
                touch_storage_worker(worker_no, state="backoff")
                await storage_worker_retry_delay()
    finally:
        status.current_session_id = None
        status.current_batch_size = 0
        touch_storage_worker(worker_no, state="stopped")
        logger.info("storage worker stopped worker=%s", worker_no)


async def storage_worker_supervisor(worker_no: int) -> None:
    while not archive_shutdown.is_set():
        try:
            await storage_worker_loop(worker_no)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = touch_storage_worker(worker_no, state="restarting")
            status.last_error_at_utc = utcnow_naive()
            status.last_error = str(exc)[:4000]
            logger.exception("storage worker crashed worker=%s; supervisor will restart it", worker_no)
            await storage_worker_retry_delay()
            continue
        if not archive_shutdown.is_set():
            status = touch_storage_worker(worker_no, state="restarting")
            status.last_error_at_utc = utcnow_naive()
            status.last_error = "storage worker exited unexpectedly"
            logger.error("storage worker exited unexpectedly worker=%s; supervisor will restart it", worker_no)
            await storage_worker_retry_delay()


async def recover_archive_handoff() -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(VideoSegment)
            .where(VideoSegment.storage_state == "copying")
            .values(storage_state="spool", archive_last_error="recovered interrupted archive copy")
        )
        await db.commit()

    spool_root = Path(settings.video_spool_root)
    if spool_root.exists():
        for session_dir in spool_root.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                session_id = uuid.UUID(session_dir.name)
            except ValueError:
                continue
            source_files = [path.name for path in (session_dir / "segments").glob("seg_*.ts")]
            candidates: list[ArchiveCandidate] = []
            async with SessionLocal() as db:
                session = await db.get(VideoSession, session_id)
                if session is not None and source_files:
                    rows = (
                        await db.execute(
                            select(VideoSegment).where(
                                VideoSegment.video_session_id == session_id,
                                VideoSegment.file_name.in_(source_files),
                                VideoSegment.storage_state == "archive_ready",
                            )
                        )
                    ).scalars().all()
                    metadata = session.metadata_json or {}
                    candidates = [
                        ArchiveCandidate(
                            segment_id=segment.id,
                            session_id=session_id,
                            event_id=session.event_id,
                            file_name=segment.file_name,
                            expected_bytes=int(segment.bytes),
                            output_root_key=str(metadata.get("output_root_key") or "root1"),
                            output_subdir=str(metadata.get("output_subdir") or "streamhub"),
                        )
                        for segment in rows
                    ]

            for candidate in candidates:
                try:
                    await asyncio.to_thread(cleanup_spool_copy_after_commit, candidate)
                except Exception:
                    logger.exception(
                        "archive recovery could not clean spool session=%s segment=%s",
                        candidate.session_id,
                        candidate.file_name,
                    )
            try:
                await refresh_archive_metadata(session_id)
                await maybe_cleanup_terminal_spool(session_id)
            except Exception:
                logger.exception("archive recovery metadata failed session=%s", session_id)
    archive_wakeup.set()


async def storage_watchdog_loop() -> None:
    while not archive_shutdown.is_set():
        try:
            free_bytes = shutil.disk_usage(Path(settings.video_spool_root)).free
            if settings.video_spool_min_free_bytes and free_bytes < settings.video_spool_min_free_bytes:
                logger.error(
                    "video spool below safety threshold free=%s threshold=%s",
                    free_bytes,
                    settings.video_spool_min_free_bytes,
                )
                for worker in list(workers.values()):
                    if not worker.stop_event.is_set():
                        await worker.request_stop("storage_full")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("video storage watchdog failed")
        try:
            await asyncio.wait_for(archive_shutdown.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


async def claim_storage_migration_job() -> int | None:
    async with SessionLocal() as db:
        job = await db.scalar(
            select(StorageMigrationJob)
            .where(StorageMigrationJob.status.in_({"queued", "running"}))
            .order_by(StorageMigrationJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            await db.rollback()
            return None
        job.status = "running"
        job.migrated_sessions = 0
        job.copied_bytes = 0
        job.current_session_id = None
        job.last_error = None
        job.started_at_utc = utcnow_naive()
        job.completed_at_utc = None
        job.updated_at = utcnow_naive()
        job_id = int(job.id)
        await db.commit()
        return job_id


async def _session_archive_bytes(session_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        segment_bytes = await db.scalar(
            select(func.coalesce(func.sum(VideoSegment.bytes), 0)).where(
                VideoSegment.video_session_id == session_id
            )
        )
        ready_part_bytes = await db.scalar(
            select(func.coalesce(func.sum(VideoPart.final_bytes), 0)).where(
                VideoPart.video_session_id == session_id,
                VideoPart.status == "ready",
            )
        )
        return int(segment_bytes or 0) + int(ready_part_bytes or 0)


async def migrate_storage_session(job_id: int, session_id: uuid.UUID) -> tuple[bool, int]:
    async with SessionLocal() as db:
        job = await db.get(StorageMigrationJob, job_id)
        session = await db.get(VideoSession, session_id)
        if job is None or session is None:
            return False, 0
        if session.status in ACTIVE_VIDEO_STATUSES:
            raise ArchiveCopyError(f"video session became active during storage migration: {session_id}")
        active_part_job = await db.scalar(
            select(VideoPartBuildJob.id)
            .join(VideoPart, VideoPart.id == VideoPartBuildJob.part_id)
            .where(
                VideoPart.video_session_id == session_id,
                VideoPartBuildJob.status.in_({"queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"}),
            )
            .limit(1)
        )
        if active_part_job is not None:
            raise ArchiveCopyError(f"video session has pending/active part build during storage migration: {session_id}")
        non_ready = await db.scalar(
            select(func.count(VideoSegment.id)).where(
                VideoSegment.video_session_id == session_id,
                VideoSegment.storage_state != "archive_ready",
            )
        )
        if int(non_ready or 0) > 0:
            raise ArchiveCopyError(f"video session has non-archive-ready segments: {session_id}")
        segment_bytes = int(
            await db.scalar(
                select(func.coalesce(func.sum(VideoSegment.bytes), 0)).where(
                    VideoSegment.video_session_id == session_id
                )
            )
            or 0
        )
        ready_part_bytes = int(
            await db.scalar(
                select(func.coalesce(func.sum(VideoPart.final_bytes), 0)).where(
                    VideoPart.video_session_id == session_id,
                    VideoPart.status == "ready",
                )
            )
            or 0
        )
        archive_bytes = segment_bytes + ready_part_bytes
        metadata = dict(session.metadata_json or {})
        current_root_key = str(metadata.get("output_root_key") or "root1")
        output_subdir = str(metadata.get("output_subdir") or "streamhub")
        event_id = session.event_id
        source_root_key = job.source_root_key
        destination_root_key = job.destination_root_key

    if current_root_key not in {source_root_key, destination_root_key}:
        logger.warning(
            "storage migration job=%s skipped session=%s because root changed to %s",
            job_id,
            session_id,
            current_root_key,
        )
        return False, 0

    source_base = session_output_base({
        "output_root_key": source_root_key,
        "output_subdir": output_subdir,
    })
    destination_base = session_output_base({
        "output_root_key": destination_root_key,
        "output_subdir": output_subdir,
    })
    source_session_root = safe_archive_session_root(source_base, event_id, session_id)
    destination_session_root = safe_archive_session_root(destination_base, event_id, session_id)

    if current_root_key == source_root_key:
        if source_session_root.exists():
            await asyncio.to_thread(
                atomic_copy_tree_verified,
                source_session_root,
                destination_session_root,
                min_free_bytes=settings.video_archive_min_free_bytes,
            )
        elif archive_bytes > 0:
            raise ArchiveCopyError(f"storage migration source is missing for session {session_id}")

        async with SessionLocal() as db:
            session = await db.get(VideoSession, session_id)
            if session is None:
                raise ArchiveCopyError(f"video session disappeared during storage migration: {session_id}")
            metadata = dict(session.metadata_json or {})
            metadata["output_root_key"] = destination_root_key
            session.metadata_json = metadata
            await db.commit()

        # Rewrite manifests/session metadata through the new root before removing
        # the old tree. This also creates the destination metadata tree for an
        # empty session.
        await refresh_archive_metadata(session_id)

    # Crash recovery path: if the DB was already switched to destination but the
    # old tree survived, verify the destination again and then clean the source.
    # A non-empty session must never be considered migrated if the destination
    # tree disappeared after the DB switch.
    if current_root_key == destination_root_key and archive_bytes > 0 and not destination_session_root.exists():
        raise ArchiveCopyError(
            f"destination tree is missing after DB migration for session {session_id}"
        )

    if source_session_root.exists():
        if not destination_session_root.exists():
            raise ArchiveCopyError(
                f"destination tree is missing after DB migration for session {session_id}"
            )
        await asyncio.to_thread(
            atomic_copy_tree_verified,
            source_session_root,
            destination_session_root,
            min_free_bytes=0,
        )
        same_location = False
        try:
            same_location = os.path.samefile(source_session_root, destination_session_root)
        except OSError:
            pass
        if not same_location:
            await asyncio.to_thread(shutil.rmtree, source_session_root)

    return True, archive_bytes


async def process_storage_migration_job(job_id: int) -> None:
    try:
        async with SessionLocal() as db:
            job = await db.get(StorageMigrationJob, job_id)
            if job is None:
                return
            raw_ids = list(job.session_ids_json or [])
        session_ids = [uuid.UUID(str(value)) for value in raw_ids]
        migrated = 0
        copied_bytes = 0
        for session_id in session_ids:
            if archive_shutdown.is_set():
                return
            async with SessionLocal() as db:
                job = await db.get(StorageMigrationJob, job_id)
                if job is None:
                    return
                job.current_session_id = session_id
                job.updated_at = utcnow_naive()
                await db.commit()

            moved, session_bytes = await migrate_storage_session(job_id, session_id)
            if moved:
                migrated += 1
                copied_bytes += session_bytes
            async with SessionLocal() as db:
                job = await db.get(StorageMigrationJob, job_id)
                if job is None:
                    return
                job.migrated_sessions = migrated
                job.copied_bytes = copied_bytes
                job.current_session_id = None
                job.updated_at = utcnow_naive()
                await db.commit()

        async with SessionLocal() as db:
            job = await db.get(StorageMigrationJob, job_id)
            if job is None:
                return
            job.status = "completed"
            job.migrated_sessions = migrated
            job.copied_bytes = copied_bytes
            job.current_session_id = None
            job.completed_at_utc = utcnow_naive()
            job.updated_at = utcnow_naive()
            await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("storage migration failed job=%s", job_id)
        async with SessionLocal() as db:
            job = await db.get(StorageMigrationJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                job.current_session_id = None
                job.completed_at_utc = utcnow_naive()
                job.updated_at = utcnow_naive()
                await db.commit()


async def storage_migration_loop() -> None:
    while not archive_shutdown.is_set():
        job_id = await claim_storage_migration_job()
        if job_id is None:
            try:
                await asyncio.wait_for(archive_shutdown.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            continue
        await process_storage_migration_job(job_id)


class RecorderWorker:
    def __init__(self, request: VideoStartRequest):
        self.request = request
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.streamlink_proc: subprocess.Popen | None = None
        self.ffmpeg_proc: subprocess.Popen | None = None
        self.stop_reason = "user_stop"

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self.run(), name=f"video-{self.request.session_id}")

    async def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        self.stop_event.set()
        if self.streamlink_proc and self.streamlink_proc.poll() is None:
            self.streamlink_proc.terminate()
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=settings.video_stop_timeout_seconds)
            except asyncio.TimeoutError:
                await self.force_stop()
                try:
                    await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
                except asyncio.TimeoutError:
                    self.task.cancel()

    async def force_stop(self) -> None:
        for proc in (self.streamlink_proc, self.ffmpeg_proc):
            if proc and proc.poll() is None:
                proc.kill()

    async def reconcile_previous_runs(self) -> int | None:
        async with SessionLocal() as db:
            runs = (
                await db.execute(
                    select(VideoRun)
                    .where(VideoRun.video_session_id == self.request.session_id)
                    .order_by(VideoRun.run_no)
                )
            ).scalars().all()
        for run in runs:
            await index_closed_segments(self.request.session_id, run.id, run.run_no)
        async with SessionLocal() as db:
            stale = (
                await db.execute(
                    select(VideoRun).where(
                        VideoRun.video_session_id == self.request.session_id,
                        VideoRun.status.in_({"starting", "running"}),
                    )
                )
            ).scalars().all()
            for run in stale:
                run.status = "closed"
                run.ended_at_utc = utcnow_naive()
                run.close_reason = "service_restart"
            if stale:
                await db.commit()
        return max((run.run_no for run in runs), default=None)

    async def create_run(self) -> VideoRun:
        run_no = await next_run_no(self.request.session_id)
        segment_start = await max_segment_no(self.request.session_id) + 1
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                raise RuntimeError("video session disappeared")
            resume_offset = int(session.coverage_end_ms or 0) if self.request.media_type == "vod" else None
            run = VideoRun(
                video_session_id=self.request.session_id,
                run_no=run_no,
                status="starting",
                started_at_utc=utcnow_naive(),
                resume_source_offset_ms=resume_offset,
                first_segment_no=segment_start,
            )
            db.add(run)
            await db.flush()
            run_id = run.id
            await db.commit()
            await db.refresh(run)
            assert run.id == run_id
            return run

    async def launch(self, run: VideoRun) -> tuple[subprocess.Popen, subprocess.Popen, object]:
        segments_dir, runs_dir, logs_dir = ensure_spool_dirs(self.request.session_id)
        start_number = await max_segment_no(self.request.session_id) + 1
        log_handle = open(logs_dir / "recorder.log", "ab", buffering=0)

        streamlink_cmd = build_streamlink_command(
            media_type=self.request.media_type,
            source_url=self.request.source_url,
            quality=self.request.quality or settings.video_stream_quality,
            resume_source_offset_ms=run.resume_source_offset_ms,
            stream_timeout_seconds=settings.video_stream_timeout_seconds,
        )
        ffmpeg_cmd = build_ffmpeg_command(
            segments_dir=segments_dir,
            runs_dir=runs_dir,
            run_no=run.run_no,
            start_number=start_number,
            segment_seconds=settings.video_segment_seconds,
        )
        log_handle.write(
            (
                f"\n[{datetime.now(UTC).isoformat()}] run={run.run_no} start_segment={start_number} "
                f"media={self.request.media_type} resume_ms={run.resume_source_offset_ms or 0}\n"
            ).encode("utf-8")
        )
        streamlink_proc = subprocess.Popen(
            streamlink_cmd,
            stdout=subprocess.PIPE,
            stderr=log_handle,
            bufsize=0,
        )
        assert streamlink_proc.stdout is not None
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=streamlink_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=log_handle,
        )
        # ffmpeg owns the read side now; closing the parent's duplicate lets EOF
        # propagate when Streamlink exits.
        streamlink_proc.stdout.close()
        self.streamlink_proc = streamlink_proc
        self.ffmpeg_proc = ffmpeg_proc
        return streamlink_proc, ffmpeg_proc, log_handle

    async def close_pipeline(self) -> None:
        if self.streamlink_proc and self.streamlink_proc.poll() is None:
            self.streamlink_proc.terminate()
            try:
                await asyncio.to_thread(self.streamlink_proc.wait, 5)
            except subprocess.TimeoutExpired:
                self.streamlink_proc.kill()
                await asyncio.to_thread(self.streamlink_proc.wait)
        if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            try:
                await asyncio.to_thread(self.ffmpeg_proc.wait, 8)
            except subprocess.TimeoutExpired:
                self.ffmpeg_proc.terminate()
                try:
                    await asyncio.to_thread(self.ffmpeg_proc.wait, 5)
                except subprocess.TimeoutExpired:
                    self.ffmpeg_proc.kill()
                    await asyncio.to_thread(self.ffmpeg_proc.wait)

    async def update_run_closed(self, run_id: int, *, close_reason: str, last_error: str | None = None) -> None:
        async with SessionLocal() as db:
            run = await db.get(VideoRun, run_id)
            if run is None:
                return
            run.status = "failed" if last_error else "closed"
            run.ended_at_utc = utcnow_naive()
            run.streamlink_exit_code = self.streamlink_proc.returncode if self.streamlink_proc else None
            run.ffmpeg_exit_code = self.ffmpeg_proc.returncode if self.ffmpeg_proc else None
            run.close_reason = close_reason
            run.last_error = last_error
            await db.commit()

    async def mark_session(self, **values) -> None:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                return
            for key, value in values.items():
                setattr(session, key, value)
            session.last_activity_at_utc = utcnow_naive()
            await db.commit()

    async def add_live_gap(self, previous_run_no: int, before_run_no: int) -> None:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                return
            session.gap_count = int(session.gap_count or 0) + 1
            db.add(
                VideoGap(
                    video_session_id=self.request.session_id,
                    after_run_no=previous_run_no,
                    before_run_no=before_run_no,
                    started_at_utc=utcnow_naive(),
                    reason="disconnect",
                    resolved=False,
                )
            )
            await db.commit()

    async def finalize_pause(self) -> None:
        await self.mark_session(status="paused", last_error=None, stop_reason=None)
        await build_spool_playlist(self.request.session_id, finished=False)
        archive_wakeup.set()

    async def finalize_control_stop(self) -> None:
        if self.stop_reason == "service_shutdown":
            await self.mark_session(status="reconnecting", last_error=None)
        elif self.stop_reason == "pause":
            await self.finalize_pause()
        else:
            await self.finalize_user_stop()

    async def finalize_user_stop(self) -> None:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                return
            session.status = "completed"
            session.ended_at_utc = utcnow_naive()
            session.stop_reason = self.stop_reason
            if self.stop_reason == "storage_full":
                session.completeness_status = "incomplete"
                session.last_error = "video spool reached the configured free-space safety threshold"
            elif self.request.media_type == "live" and not session.gap_count and not session.last_error:
                session.completeness_status = "complete"
                session.required_end_ms = session.coverage_end_ms
            else:
                session.completeness_status = "incomplete"
            session.last_activity_at_utc = utcnow_naive()
            await db.commit()
        await build_spool_playlist(self.request.session_id, finished=True)
        archive_wakeup.set()
        try:
            await refresh_archive_metadata(self.request.session_id)
            await maybe_cleanup_terminal_spool(self.request.session_id)
        except Exception:
            logger.exception("failed final archive metadata session=%s", self.request.session_id)

    async def finalize_vod_eof(self) -> None:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                return
            source_duration = self.request.source_duration_ms or session.required_end_ms
            coverage = int(session.coverage_end_ms or 0)
            tolerance = max(5000, settings.video_segment_seconds * 1500)
            verified = source_duration is None or coverage >= max(0, int(source_duration) - tolerance)
            session.status = "completed"
            session.ended_at_utc = utcnow_naive()
            session.stop_reason = "vod_eof"
            session.completeness_status = "complete" if verified and not session.gap_count else "incomplete"
            session.last_error = None if verified else f"VOD EOF coverage {coverage}ms is below source duration {source_duration}ms"
            session.last_activity_at_utc = utcnow_naive()
            await db.commit()
        await build_spool_playlist(self.request.session_id, finished=True)
        archive_wakeup.set()
        try:
            await refresh_archive_metadata(self.request.session_id)
            await maybe_cleanup_terminal_spool(self.request.session_id)
        except Exception:
            logger.exception("failed final archive metadata session=%s", self.request.session_id)

    async def run(self) -> None:
        previous_run_no = await self.reconcile_previous_runs()
        await self.mark_session(status="arming", completeness_status="collecting", last_error=None)
        try:
            while not self.stop_event.is_set():
                run = await self.create_run()
                if self.request.media_type == "live" and previous_run_no is not None:
                    await self.add_live_gap(previous_run_no, run.run_no)
                previous_run_no = run.run_no
                try:
                    streamlink_proc, ffmpeg_proc, log_handle = await self.launch(run)
                except Exception as exc:
                    error = f"recorder launch failed: {exc}"
                    await self.update_run_closed(run.id, close_reason="launch_error", last_error=error)
                    await self.mark_session(status="reconnecting", last_error=error)
                    if self.stop_event.is_set():
                        await self.finalize_control_stop()
                        break
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=settings.video_reconnect_seconds)
                    except asyncio.TimeoutError:
                        continue
                    await self.finalize_control_stop()
                    break

                async with SessionLocal() as db:
                    db_run = await db.get(VideoRun, run.id)
                    if db_run:
                        db_run.status = "running"
                    session = await db.get(VideoSession, self.request.session_id)
                    if session:
                        session.status = "recording"
                        session.last_error = None
                        session.last_activity_at_utc = utcnow_naive()
                    await db.commit()

                try:
                    while not self.stop_event.is_set():
                        await index_closed_segments(self.request.session_id, run.id, run.run_no)
                        if streamlink_proc.poll() is not None or ffmpeg_proc.poll() is not None:
                            break
                        await asyncio.sleep(1.0)
                    await self.close_pipeline()
                    await index_closed_segments(self.request.session_id, run.id, run.run_no)
                finally:
                    try:
                        log_handle.close()
                    except Exception:
                        pass

                if self.stop_event.is_set():
                    await self.update_run_closed(run.id, close_reason=self.stop_reason)
                    await self.finalize_control_stop()
                    break

                clean_eof = streamlink_proc.returncode == 0 and ffmpeg_proc.returncode == 0
                if self.request.media_type == "vod" and clean_eof:
                    await self.update_run_closed(run.id, close_reason="vod_eof")
                    await self.finalize_vod_eof()
                    break

                error = (
                    f"pipeline exited streamlink={streamlink_proc.returncode} ffmpeg={ffmpeg_proc.returncode}; "
                    f"retry in {settings.video_reconnect_seconds:g}s"
                )
                await self.update_run_closed(run.id, close_reason="disconnect", last_error=error)
                await self.mark_session(status="reconnecting", last_error=error)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=settings.video_reconnect_seconds)
                except asyncio.TimeoutError:
                    continue
                await self.finalize_control_stop()
                break
        except asyncio.CancelledError:
            await self.force_stop()
            raise
        except Exception as exc:
            logger.exception("video recorder worker failed session=%s", self.request.session_id)
            await self.mark_session(status="failed", completeness_status="failed", last_error=str(exc), ended_at_utc=utcnow_naive())
        finally:
            self.streamlink_proc = None
            self.ffmpeg_proc = None
            workers.pop(self.request.session_id, None)


workers: dict[uuid.UUID, RecorderWorker] = {}


async def start_worker(request: VideoStartRequest) -> RecorderWorker:
    existing = workers.get(request.session_id)
    if existing and existing.task and not existing.task.done():
        return existing
    worker = RecorderWorker(request)
    workers[request.session_id] = worker
    worker.start()
    return worker


async def recover_active_sessions() -> None:
    await asyncio.sleep(0.5)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(VideoSession).where(VideoSession.status.in_(ACTIVE_VIDEO_STATUSES), VideoSession.deleted_at_utc.is_(None))
            )
        ).scalars().all()
    for row in rows:
        media_type = (row.metadata_json or {}).get("media_type")
        if media_type not in {"live", "vod"}:
            logger.error("cannot recover video session %s: media_type missing", row.id)
            await RecorderWorker(
                VideoStartRequest(
                    session_id=row.id,
                    event_id=row.event_id,
                    media_type="vod",
                    source_url=row.source_url,
                    quality=row.quality,
                )
            ).mark_session(status="failed", completeness_status="failed", last_error="recovery metadata missing media_type")
            continue
        await start_worker(
            VideoStartRequest(
                session_id=row.id,
                event_id=row.event_id,
                media_type=media_type,
                source_url=row.source_url,
                quality=row.quality,
                source_duration_ms=row.required_end_ms,
            )
        )
        logger.info("recovered video session %s media=%s", row.id, media_type)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Path(settings.video_spool_root).mkdir(parents=True, exist_ok=True)
    for output_root in enabled_output_roots().values():
        output_root.mkdir(parents=True, exist_ok=True)
    archive_shutdown.clear()
    purge_reconcile = await reconcile_purge_quarantines(restore_referenced=True)
    if purge_reconcile["found"]:
        logger.warning("purge quarantine startup reconcile: %s", purge_reconcile)
    await recover_archive_handoff()
    storage_worker_status.clear()
    storage_worker_tasks.clear()
    for worker_no in range(1, settings.video_storage_copy_workers + 1):
        storage_worker_tasks[worker_no] = asyncio.create_task(
            storage_worker_supervisor(worker_no),
            name=f"video-storage-{worker_no}",
        )
    storage_tasks = list(storage_worker_tasks.values())
    watchdog = asyncio.create_task(storage_watchdog_loop(), name="video-storage-watchdog")
    migration = asyncio.create_task(storage_migration_loop(), name="video-storage-migration")
    purge_reaper = asyncio.create_task(purge_quarantine_reaper_loop(), name="video-purge-reaper")
    recovery = asyncio.create_task(recover_active_sessions())
    try:
        yield
    finally:
        recovery.cancel()
        for worker in list(workers.values()):
            try:
                await worker.request_stop("service_shutdown")
            except Exception:
                logger.exception("failed stopping video worker session=%s", worker.request.session_id)
        archive_shutdown.set()
        archive_wakeup.set()
        watchdog.cancel()
        migration.cancel()
        purge_reaper.cancel()
        for task in storage_tasks:
            task.cancel()
        await asyncio.gather(
            watchdog, migration, purge_reaper, *storage_tasks, return_exceptions=True
        )
        storage_worker_tasks.clear()


app = FastAPI(title="StreamHub Video Recorder", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict:
    missing = [name for name in ("streamlink", "ffmpeg", "ffprobe") if shutil.which(name) is None]
    spool_root = Path(settings.video_spool_root)
    output_roots = enabled_output_roots()
    spool_writable = directory_is_writable(spool_root)
    output_writable = {key: directory_is_writable(path) for key, path in output_roots.items()}
    if missing or not spool_writable or not all(output_writable.values()):
        raise HTTPException(
            503,
            detail={
                "missing": missing,
                "spool_writable": spool_writable,
                "output_writable": output_writable,
            },
        )
    try:
        async with SessionLocal() as db:
            await db.scalar(select(func.count()).select_from(VideoSession))
            archive_rows = (
                await db.execute(
                    select(VideoSegment.storage_state, func.count(VideoSegment.id))
                    .where(VideoSegment.storage_state.in_({"spool", "copying"}))
                    .group_by(VideoSegment.storage_state)
                )
            ).all()
    except Exception as exc:
        raise HTTPException(503, detail=f"database unavailable: {exc}") from exc

    archive_backlog = {str(state): int(count or 0) for state, count in archive_rows}
    worker_status = storage_worker_status_payload()
    dead_workers = [
        worker_no
        for worker_no in range(1, settings.video_storage_copy_workers + 1)
        if worker_no not in storage_worker_tasks or storage_worker_tasks[worker_no].done()
    ]
    if dead_workers:
        raise HTTPException(
            503,
            detail={
                "error": "video storage worker supervisor is not running",
                "dead_workers": dead_workers,
                "storage_workers": worker_status,
                "archive_backlog": archive_backlog,
            },
        )
    return {
        "status": "ready",
        "spool_root": str(spool_root),
        "output_roots": {key: str(path) for key, path in output_roots.items()},
        "storage_copy_workers": settings.video_storage_copy_workers,
        "storage_workers": worker_status,
        "archive_backlog": archive_backlog,
        "archive_batch_segments": settings.video_archive_batch_segments,
    }


@app.post("/internal/v1/video-sessions", dependencies=[Depends(require_internal_token)])
async def start_video_session(payload: VideoStartRequest) -> dict:
    spool_free = shutil.disk_usage(Path(settings.video_spool_root)).free
    if settings.video_spool_min_free_bytes and spool_free < settings.video_spool_min_free_bytes:
        raise HTTPException(507, "video spool is below the configured free-space safety threshold")
    async with SessionLocal() as db:
        row = await db.get(VideoSession, payload.session_id)
        if row is None:
            raise HTTPException(404, "video session not found")
        if row.event_id != payload.event_id:
            raise HTTPException(409, "video session event mismatch")
        if row.status not in {"new", "arming", "reconnecting"}:
            if row.status == "recording":
                return {"status": "already_active", "session_id": str(row.id)}
            raise HTTPException(409, f"cannot start video session in state {row.status}")
    await start_worker(payload)
    return {"status": "started", "session_id": str(payload.session_id)}


@app.post("/internal/v1/video-sessions/{session_id}/pause", dependencies=[Depends(require_internal_token)])
async def pause_video_session(session_id: uuid.UUID) -> dict:
    worker = workers.get(session_id)
    if worker:
        await worker.request_stop("pause")

    async with SessionLocal() as db:
        row = await db.get(VideoSession, session_id)
        if row is None:
            raise HTTPException(404, "video session not found")
        if row.status == "paused":
            return {"status": "paused", "session_id": str(session_id)}
        if row.status not in ACTIVE_VIDEO_STATUSES:
            raise HTTPException(409, f"cannot pause video session in state {row.status}")
        # No runtime worker means capture is already not producing I/O. Persist
        # the requested paused state instead of leaving a stale recording row.
        row.status = "paused"
        row.last_error = None
        row.stop_reason = None
        row.last_activity_at_utc = utcnow_naive()
        await db.commit()
    archive_wakeup.set()
    return {"status": "paused", "session_id": str(session_id)}


@app.post("/internal/v1/video-sessions/{session_id}/resume", dependencies=[Depends(require_internal_token)])
async def resume_video_session(session_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        row = await db.get(VideoSession, session_id)
        if row is None:
            raise HTTPException(404, "video session not found")
        if row.status in ACTIVE_VIDEO_STATUSES:
            return {"status": "already_active", "session_id": str(session_id)}
        if row.status != "paused":
            raise HTTPException(409, f"cannot resume video session in state {row.status}")
        media_type = str((row.metadata_json or {}).get("media_type") or "")
        if media_type not in {"live", "vod"}:
            raise HTTPException(409, "video session recovery metadata is missing media_type")
        request = VideoStartRequest(
            session_id=row.id,
            event_id=row.event_id,
            media_type=media_type,
            source_url=row.source_url,
            quality=row.quality,
            source_duration_ms=row.required_end_ms,
        )
        # Persist the transition before returning so the public API/Extension
        # cannot observe a stale paused row after Resume succeeds.
        row.status = "arming"
        row.last_error = None
        row.last_activity_at_utc = utcnow_naive()
        await db.commit()

    await start_worker(request)
    return {"status": "resumed", "session_id": str(session_id)}


@app.post("/internal/v1/video-sessions/{session_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_video_session(session_id: uuid.UUID, payload: StopRequest) -> dict:
    worker = workers.get(session_id)
    if worker:
        await worker.request_stop(payload.reason)
        async with SessionLocal() as db:
            row = await db.get(VideoSession, session_id)
            if row is not None and row.status in ACTIVE_VIDEO_STATUSES:
                row.status = "completed"
                row.completeness_status = "incomplete"
                row.stop_reason = payload.reason
                row.ended_at_utc = utcnow_naive()
                row.last_activity_at_utc = utcnow_naive()
                await db.commit()
        return {"status": "stopped", "session_id": str(session_id)}

    async with SessionLocal() as db:
        row = await db.get(VideoSession, session_id)
        if row is None:
            raise HTTPException(404, "video session not found")
        if row.status in {"completed", "failed", "soft_deleted"}:
            return {"status": row.status, "session_id": str(session_id)}
        row.status = "completed"
        row.completeness_status = "incomplete"
        row.stop_reason = payload.reason
        row.ended_at_utc = utcnow_naive()
        row.last_activity_at_utc = utcnow_naive()
        await db.commit()
    return {"status": "stopped_without_runtime", "session_id": str(session_id)}


def purge_archive_root(ticket: PurgeTicket) -> Path:
    root = configured_output_root(ticket.output_root_key).resolve()
    subdir = normalize_output_subdir(ticket.output_subdir)
    base = (root / Path(*subdir.parts)).resolve()
    base.relative_to(root)
    return safe_archive_session_root(base, ticket.event_id, ticket.session_id)

async def wait_for_archive_copy_idle(session_id: uuid.UUID, *, timeout_seconds: float = 30.0) -> None:
    """Wait for an already-claimed archive batch to leave the copying state.

    Soft-deleted sessions are excluded from new archive claims, so once the
    in-flight copying rows drain no new archive I/O can start for this session.
    This closes the delete-vs-archive race on Windows bind mounts.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        async with SessionLocal() as db:
            copying = await db.scalar(
                select(func.count(VideoSegment.id)).where(
                    VideoSegment.video_session_id == session_id,
                    VideoSegment.storage_state == "copying",
                )
            )
        if int(copying or 0) == 0:
            return
        if loop.time() >= deadline:
            raise HTTPException(
                409,
                "video archive handoff is still finishing; retry permanent delete shortly",
            )
        await asyncio.sleep(0.25)


async def quarantine_directory_with_retry(path: Path, token: str) -> bool:
    """Retry transient Windows bind-mount rename locks for a short window."""
    attempts = 20
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(quarantine_directory, path, token)
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(0.25)
    return False


async def delete_quarantined_directory_with_retry(path: Path, token: str) -> None:
    """Retry transient Windows/bind-mount locks while physically purging files."""
    attempts = 30
    for attempt in range(attempts):
        try:
            await asyncio.to_thread(delete_quarantined_directory, path, token)
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(min(0.25 * (attempt + 1), 2.0))


async def reconcile_purge_quarantines(*, restore_referenced: bool) -> dict[str, int]:
    """Reconcile purge quarantine directories against durable DB state.

    A recorder/API crash can happen after the directory rename but before or
    after the DB transaction commits.  Existing VideoSession rows therefore
    mean the quarantine must be restored; missing rows mean the DB purge won
    and the quarantine can be deleted safely.
    """
    roots = [Path(settings.video_spool_root), *enabled_output_roots().values()]
    entries: dict[str, tuple[Path, Path, uuid.UUID, str]] = {}
    for root in roots:
        for quarantine, original, session_id, token in await asyncio.to_thread(
            find_purge_quarantines, root
        ):
            entries[str(quarantine.resolve())] = (quarantine, original, session_id, token)
    if not entries:
        return {"found": 0, "restored": 0, "deleted": 0, "failed": 0}

    session_ids = {entry[2] for entry in entries.values()}
    async with SessionLocal() as db:
        existing = set(
            (
                await db.execute(
                    select(VideoSession.id).where(VideoSession.id.in_(session_ids))
                )
            ).scalars().all()
        )

    restored = 0
    deleted = 0
    failed = 0
    for quarantine, original, session_id, token in entries.values():
        try:
            if session_id in existing:
                if not restore_referenced:
                    continue
                await asyncio.to_thread(restore_quarantined_directory, original, token)
                restored += 1
                logger.warning(
                    "restored referenced purge quarantine session=%s path=%s",
                    session_id,
                    quarantine,
                )
            else:
                await delete_quarantined_directory_with_retry(original, token)
                deleted += 1
                logger.warning(
                    "deleted orphaned purge quarantine session=%s path=%s",
                    session_id,
                    quarantine,
                )
        except Exception:
            failed += 1
            logger.exception(
                "failed reconciling purge quarantine session=%s path=%s",
                session_id,
                quarantine,
            )
    return {"found": len(entries), "restored": restored, "deleted": deleted, "failed": failed}


async def purge_quarantine_reaper_loop() -> None:
    """Periodically retry cleanup of DB-orphaned purge directories."""
    while True:
        await asyncio.sleep(60.0)
        try:
            await reconcile_purge_quarantines(restore_referenced=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("purge quarantine reaper failed")


@app.post(
    "/internal/v1/video-sessions/{session_id}/purge-quarantine",
    dependencies=[Depends(require_internal_token)],
)
async def quarantine_video_session_for_purge(session_id: uuid.UUID) -> dict:
    worker = workers.get(session_id)
    if worker is not None and worker.task is not None and not worker.task.done():
        raise HTTPException(409, "active video worker must be stopped before purge")
    async with SessionLocal() as db:
        row = await db.get(VideoSession, session_id)
        if row is None:
            raise HTTPException(404, "video session not found")
        if row.status in ACTIVE_VIDEO_STATUSES:
            raise HTTPException(409, "active video session must be stopped before purge")
        if row.deleted_at_utc is None:
            raise HTTPException(409, "video session must be soft-deleted before purge")
        metadata = row.metadata_json or {}
        ticket = PurgeTicket(
            token=uuid.uuid4().hex,
            session_id=row.id,
            event_id=row.event_id,
            output_root_key=str(metadata.get("output_root_key") or "root1"),
            output_subdir=str(metadata.get("output_subdir") or "streamhub"),
        )

    # A batch may have been claimed immediately before the session was moved
    # to Trash. Let that already-started copy finish before renaming either
    # directory. New claims are impossible because deleted sessions are filtered
    # out by claim_archive_batch().
    await wait_for_archive_copy_idle(session_id)

    archive_root = purge_archive_root(ticket)
    spool_root = safe_session_root(session_id)
    try:
        ticket.archive_quarantined = await quarantine_directory_with_retry(
            archive_root, ticket.token
        )
        ticket.spool_quarantined = await quarantine_directory_with_retry(
            spool_root, ticket.token
        )
    except Exception as exc:
        try:
            if ticket.spool_quarantined:
                await asyncio.to_thread(restore_quarantined_directory, spool_root, ticket.token)
            if ticket.archive_quarantined:
                await asyncio.to_thread(restore_quarantined_directory, archive_root, ticket.token)
        except Exception:
            logger.exception("failed rolling back purge quarantine session=%s", session_id)
        raise HTTPException(500, f"video purge quarantine failed: {exc}") from exc
    return ticket.model_dump(mode="json")


@app.post("/internal/v1/video-purge/restore", dependencies=[Depends(require_internal_token)])
async def restore_video_purge(ticket: PurgeTicket) -> dict:
    archive_root = purge_archive_root(ticket)
    spool_root = safe_session_root(ticket.session_id)
    if ticket.spool_quarantined:
        await asyncio.to_thread(restore_quarantined_directory, spool_root, ticket.token)
    if ticket.archive_quarantined:
        await asyncio.to_thread(restore_quarantined_directory, archive_root, ticket.token)
    return {"status": "restored", "session_id": str(ticket.session_id)}


@app.post("/internal/v1/video-purge/finalize", dependencies=[Depends(require_internal_token)])
async def finalize_video_purge(ticket: PurgeTicket) -> dict:
    archive_root = purge_archive_root(ticket)
    spool_root = safe_session_root(ticket.session_id)
    if ticket.spool_quarantined:
        await delete_quarantined_directory_with_retry(spool_root, ticket.token)
    if ticket.archive_quarantined:
        await delete_quarantined_directory_with_retry(archive_root, ticket.token)
    return {"status": "deleted", "session_id": str(ticket.session_id)}
