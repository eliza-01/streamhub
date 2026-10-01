from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import VideoGap, VideoRun, VideoSegment, VideoSession
from streamhub_common.security import require_internal_token
from streamhub_common.settings import get_settings

from app.commands import build_ffmpeg_command, build_streamlink_command, segment_no_from_name

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
    db_max = int(value or 0)
    segments_dir, _runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    spool_max = 0
    for path in segments_dir.glob("seg_*.ts*"):
        number = segment_no_from_name(path.name)
        if number is not None:
            spool_max = max(spool_max, number)
    return max(db_max, spool_max)


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


def playlist_segment_numbers(session_id: uuid.UUID, run_no: int) -> set[int]:
    _segments_dir, runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    playlist = runs_dir / f"run_{run_no:06d}.m3u8"
    if not playlist.exists():
        return set()
    result: set[int] = set()
    try:
        lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        name = Path(value).name
        if not name.startswith("seg_") or not name.endswith(".ts"):
            continue
        try:
            result.add(int(name[4:-3]))
        except ValueError:
            continue
    return result


async def index_closed_segments(session_id: uuid.UUID, run_id: int, run_no: int) -> int:
    segments_dir, _runs_dir, _logs_dir = ensure_spool_dirs(session_id)
    allowed_segment_numbers = playlist_segment_numbers(session_id, run_no)
    if not allowed_segment_numbers:
        return 0
    async with SessionLocal() as db:
        existing = set(
            (
                await db.execute(
                    select(VideoSegment.segment_no).where(VideoSegment.video_session_id == session_id)
                )
            ).scalars().all()
        )
        session = await db.get(VideoSession, session_id)
        run = await db.get(VideoRun, run_id)
        if session is None or run is None:
            return 0

        created = 0
        timeline_cursor = int(session.coverage_end_ms or 0)
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
            source_start = timeline_cursor if session.metadata_json and session.metadata_json.get("media_type") == "vod" else None
            source_end = timeline_cursor + duration_ms if source_start is not None else None
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
            existing.add(segment_no)
            created += 1
            run.first_segment_no = segment_no if run.first_segment_no is None else min(run.first_segment_no, segment_no)
            run.last_segment_no = segment_no if run.last_segment_no is None else max(run.last_segment_no, segment_no)

        if created:
            session.duration_recorded_ms = timeline_cursor
            session.coverage_start_ms = 0 if session.coverage_start_ms is None else session.coverage_start_ms
            session.coverage_end_ms = timeline_cursor
            session.last_activity_at_utc = utcnow_naive()
            await db.commit()
        else:
            await db.rollback()

    if created:
        await build_spool_playlist(session_id)
    return created





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

    async def finalize_user_stop(self) -> None:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, self.request.session_id)
            if session is None:
                return
            session.status = "completed"
            session.ended_at_utc = utcnow_naive()
            session.stop_reason = self.stop_reason
            if self.request.media_type == "live" and not session.gap_count and not session.last_error:
                session.completeness_status = "complete"
                session.required_end_ms = session.coverage_end_ms
            else:
                session.completeness_status = "incomplete"
            session.last_activity_at_utc = utcnow_naive()
            await db.commit()
        await build_spool_playlist(self.request.session_id, finished=True)

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
                        if self.stop_reason == "service_shutdown":
                            await self.mark_session(status="reconnecting", last_error=None)
                        else:
                            await self.finalize_user_stop()
                        break
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=settings.video_reconnect_seconds)
                    except asyncio.TimeoutError:
                        continue
                    if self.stop_reason == "service_shutdown":
                        await self.mark_session(status="reconnecting", last_error=None)
                    else:
                        await self.finalize_user_stop()
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
                    if self.stop_reason == "service_shutdown":
                        await self.mark_session(status="reconnecting", last_error=None)
                    else:
                        await self.finalize_user_stop()
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
                if self.stop_reason == "service_shutdown":
                    await self.mark_session(status="reconnecting", last_error=None)
                else:
                    await self.finalize_user_stop()
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


app = FastAPI(title="StreamHub Video Recorder", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict:
    missing = [name for name in ("streamlink", "ffmpeg", "ffprobe") if shutil.which(name) is None]
    root = Path(settings.video_spool_root)
    root.mkdir(parents=True, exist_ok=True)
    writable = os.access(root, os.W_OK)
    if missing or not writable:
        raise HTTPException(503, detail={"missing": missing, "spool_writable": writable})
    try:
        async with SessionLocal() as db:
            await db.scalar(select(func.count()).select_from(VideoSession))
    except Exception as exc:
        raise HTTPException(503, detail=f"database unavailable: {exc}") from exc
    return {"status": "ready", "spool_root": str(root)}


@app.post("/internal/v1/video-sessions", dependencies=[Depends(require_internal_token)])
async def start_video_session(payload: VideoStartRequest) -> dict:
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
