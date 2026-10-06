from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update

from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import (
    VideoPart,
    VideoPartBuildJob,
    VideoPartSegment,
    VideoProxyPartSource,
    TelegramVideoPartBinding,
    VideoSegment,
    VideoSession,
)
from streamhub_common.security import require_internal_token
from streamhub_common.settings import get_settings

from .core import fsync_directory, safe_child

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("streamhub.video_part_builder")

ACTIVE_CAPTURE_STATUSES = frozenset({"arming", "recording", "reconnecting"})
CLAIMABLE_JOB_STATUSES = frozenset({"queued", "waiting_capture_idle", "suspended_for_capture"})
BUILDING_JOB_STATUSES = frozenset({"building", "verifying", "suspended_for_capture"})
TERMINAL_PART_STATUSES = frozenset({"ready", "failed", "cancelled"})
INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class BuildCancelled(RuntimeError):
    pass


class SegmentReplacementPayload(BaseModel):
    copy_directory: str = Field(min_length=1, max_length=1024)


def _telegram_channel_id() -> int:
    raw = str(settings.telegram_channel_id or "").strip()
    if not raw:
        raise HTTPException(409, "Telegram channel is not configured")
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(409, "TELEGRAM_CHANNEL_ID must be numeric") from exc


def _normalize_path_text(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/")


def resolve_copy_directory(raw_directory: str) -> tuple[Path, str]:
    raw = raw_directory.strip()
    if not raw:
        raise HTTPException(400, "copy_directory is required")
    normalized = _normalize_path_text(raw)
    mappings: list[tuple[Path, str | None]] = [
        (Path(settings.video_output_root_1).resolve(), os.getenv("VIDEO_OUTPUT_ROOT_1_HOST")),
        (Path(settings.video_output_root_2).resolve(), os.getenv("VIDEO_OUTPUT_ROOT_2_HOST")),
    ]
    if settings.video_output_root_3_enabled:
        mappings.append((Path(settings.video_output_root_3).resolve(), os.getenv("VIDEO_OUTPUT_ROOT_3_HOST")))

    direct = Path(raw)
    if direct.is_absolute():
        resolved = direct.resolve()
        for container_root, _host_root in mappings:
            try:
                resolved.relative_to(container_root)
                if not resolved.is_dir():
                    raise HTTPException(409, f"copy directory does not exist: {raw}")
                return resolved, raw
            except ValueError:
                continue

    lowered = normalized.casefold()
    for container_root, host_root in mappings:
        if not host_root:
            continue
        host_normalized = _normalize_path_text(host_root)
        host_lowered = host_normalized.casefold()
        if lowered != host_lowered and not lowered.startswith(host_lowered + "/"):
            continue
        suffix = normalized[len(host_normalized):].lstrip("/")
        resolved = (container_root / Path(suffix)).resolve()
        try:
            resolved.relative_to(container_root)
        except ValueError as exc:
            raise HTTPException(400, "copy_directory escapes configured output root") from exc
        if not resolved.is_dir():
            raise HTTPException(409, f"copy directory does not exist: {raw}")
        return resolved, raw

    raise HTTPException(409, "copy_directory must be inside a configured VIDEO_OUTPUT_ROOT_1/2/3 host directory")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


async def unlink_with_retries(path: Path, *, attempts: int = 6) -> str | None:
    for attempt in range(attempts):
        try:
            path.unlink()
            return None
        except FileNotFoundError:
            return None
        except OSError as exc:
            if attempt >= attempts - 1:
                return f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.15 * (attempt + 1))
    return "unlink failed"


async def require_linked_ready_part(db, part_id: uuid.UUID) -> tuple[VideoPart, VideoSession]:
    part = await db.get(VideoPart, part_id)
    if part is None:
        raise HTTPException(404, "part not found")
    if part.status != "ready":
        raise HTTPException(409, "part must be ready")
    binding = await db.get(TelegramVideoPartBinding, part_id)
    if binding is None or int(binding.channel_id) != _telegram_channel_id():
        raise HTTPException(409, "part must be Telegram: linked")
    session = await db.get(VideoSession, part.video_session_id)
    if session is None:
        raise HTTPException(409, "video session is missing")
    return part, session


class CaptureGate:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self.requested = False
        self.active_jobs: set[int] = set()
        self.suspended_jobs: set[int] = set()
        self.active_parts: set[uuid.UUID] = set()
        self.cancel_parts: set[uuid.UUID] = set()

    async def register(self, job_id: int, part_id: uuid.UUID) -> None:
        async with self._condition:
            self.active_jobs.add(job_id)
            self.active_parts.add(part_id)
            self._condition.notify_all()

    async def unregister(self, job_id: int, part_id: uuid.UUID) -> None:
        async with self._condition:
            self.active_jobs.discard(job_id)
            self.suspended_jobs.discard(job_id)
            self.active_parts.discard(part_id)
            self.cancel_parts.discard(part_id)
            self._condition.notify_all()

    async def before_io(self, job_id: int, part_id: uuid.UUID) -> None:
        async with self._condition:
            if part_id in self.cancel_parts:
                raise BuildCancelled("cancel requested")
            if self.requested:
                self.suspended_jobs.add(job_id)
                self._condition.notify_all()
                while self.requested:
                    await self._condition.wait()
                    if part_id in self.cancel_parts:
                        raise BuildCancelled("cancel requested")
                self.suspended_jobs.discard(job_id)
                self._condition.notify_all()

    async def request_cancel(self, part_id: uuid.UUID) -> None:
        async with self._condition:
            self.cancel_parts.add(part_id)
            self._condition.notify_all()

    async def clear_cancel(self, part_id: uuid.UUID) -> None:
        async with self._condition:
            self.cancel_parts.discard(part_id)
            self._condition.notify_all()

    async def quiesce(self, timeout_seconds: float = 30.0) -> None:
        async with self._condition:
            self.requested = True
            self._condition.notify_all()

            async def wait_until_suspended() -> None:
                while not self.active_jobs.issubset(self.suspended_jobs):
                    await self._condition.wait()

            try:
                await asyncio.wait_for(wait_until_suspended(), timeout=timeout_seconds)
            except TimeoutError as exc:
                raise HTTPException(503, "part-builder could not quiesce active builders in time") from exc

    async def release(self) -> None:
        async with self._condition:
            self.requested = False
            self.suspended_jobs.clear()
            self._condition.notify_all()


gate = CaptureGate()
wakeup = asyncio.Event()
shutdown = asyncio.Event()
worker_tasks: list[asyncio.Task] = []
monitor_task: asyncio.Task | None = None


def now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def configured_output_root(root_key: str) -> Path:
    roots = {"root1": Path(settings.video_output_root_1)}
    if settings.video_output_root_2_enabled:
        roots["root2"] = Path(settings.video_output_root_2)
    if settings.video_output_root_3_enabled:
        roots["root3"] = Path(settings.video_output_root_3)
    root = roots.get(root_key)
    if root is None:
        raise RuntimeError(f"output root is not enabled: {root_key}")
    return root


async def capture_active() -> bool:
    if settings.part_build_allow_during_capture:
        return False
    async with SessionLocal() as db:
        session_id = await db.scalar(
            select(VideoSession.id)
            .where(VideoSession.deleted_at_utc.is_(None), VideoSession.status.in_(ACTIVE_CAPTURE_STATUSES))
            .limit(1)
        )
        return session_id is not None


async def update_waiting_jobs() -> None:
    async with SessionLocal() as db:
        part_ids = (
            await db.execute(
                select(VideoPartBuildJob.part_id).where(VideoPartBuildJob.status == "queued").limit(100)
            )
        ).scalars().all()
        if not part_ids:
            return
        await db.execute(
            update(VideoPartBuildJob)
            .where(VideoPartBuildJob.part_id.in_(part_ids), VideoPartBuildJob.status == "queued")
            .values(status="waiting_capture_idle", phase="waiting_capture_idle", updated_at=now_utc_naive())
        )
        await db.execute(
            update(VideoPart)
            .where(VideoPart.id.in_(part_ids), VideoPart.status == "queued")
            .values(status="waiting_capture_idle", updated_at=now_utc_naive())
        )
        await db.commit()


async def claim_job() -> tuple[int, uuid.UUID] | None:
    if await capture_active():
        await update_waiting_jobs()
        return None

    now = now_utc_naive()
    async with SessionLocal() as db:
        stmt = (
            select(VideoPartBuildJob)
            .where(
                or_(
                    VideoPartBuildJob.status.in_(CLAIMABLE_JOB_STATUSES),
                    (
                        VideoPartBuildJob.status.in_({"building", "verifying"})
                        & (VideoPartBuildJob.lease_expires_at_utc < now)
                    ),
                )
            )
            .order_by(VideoPartBuildJob.created_at, VideoPartBuildJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = await db.scalar(stmt)
        if job is None:
            await db.rollback()
            return None
        part = await db.get(VideoPart, job.part_id)
        if part is None:
            await db.execute(delete(VideoPartBuildJob).where(VideoPartBuildJob.id == job.id))
            await db.commit()
            return None
        if job.cancel_requested:
            job.status = "cancelled"
            job.phase = "cancelled"
            job.completed_at_utc = now
            part.status = "cancelled"
            part.last_error = "cancel requested before build"
            await db.commit()
            return None

        job.status = "building"
        job.phase = "building"
        job.attempts = int(job.attempts or 0) + 1
        job.lease_owner = INSTANCE_ID
        job.lease_expires_at_utc = now + timedelta(seconds=settings.part_build_lease_seconds)
        job.heartbeat_at_utc = now
        job.started_at_utc = job.started_at_utc or now
        job.last_error = None
        part.status = "building"
        part.last_error = None
        await db.commit()
        return int(job.id), part.id


async def load_build_inputs(part_id: uuid.UUID) -> tuple[VideoPart, VideoSession, list[tuple[VideoPartSegment, VideoSegment]]]:
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise RuntimeError("part disappeared")
        session = await db.get(VideoSession, part.video_session_id)
        if session is None:
            raise RuntimeError("video session disappeared")
        reservation_rows = (
            await db.execute(
                select(VideoPartSegment, VideoSegment)
                .join(VideoSegment, VideoSegment.id == VideoPartSegment.segment_id)
                .where(VideoPartSegment.part_id == part_id)
                .order_by(VideoPartSegment.segment_no)
            )
        ).all()
        pairs = [(reservation, segment) for reservation, segment in reservation_rows]
        if not pairs:
            raise RuntimeError("part has no reserved segments")
        return part, session, pairs


async def progress(job_id: int, part_id: uuid.UUID, written: int, phase: str) -> None:
    now = now_utc_naive()
    async with SessionLocal() as db:
        job = await db.get(VideoPartBuildJob, job_id)
        part = await db.get(VideoPart, part_id)
        if job is None or part is None:
            return
        job.status = phase if phase in {"verifying", "suspended_for_capture"} else "building"
        job.phase = phase
        job.progress_bytes = written
        job.heartbeat_at_utc = now
        job.lease_expires_at_utc = now + timedelta(seconds=settings.part_build_lease_seconds)
        part.status = job.status
        await db.commit()


async def mark_suspended(job_id: int, part_id: uuid.UUID, written: int) -> None:
    await progress(job_id, part_id, written, "suspended_for_capture")


async def maybe_wait_gate(
    job_id: int,
    part_id: uuid.UUID,
    written: int,
    *,
    resume_phase: str = "building",
) -> None:
    was_requested = gate.requested
    if was_requested:
        await mark_suspended(job_id, part_id, written)
    await gate.before_io(job_id, part_id)
    if was_requested:
        await progress(job_id, part_id, written, resume_phase)


async def db_cancel_requested(job_id: int) -> bool:
    async with SessionLocal() as db:
        value = await db.scalar(select(VideoPartBuildJob.cancel_requested).where(VideoPartBuildJob.id == job_id))
        return bool(value)



async def load_proxy_inputs(part_id: uuid.UUID) -> tuple[VideoPart, VideoSession, list[VideoPart]]:
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise RuntimeError("proxy part disappeared")
        session = await db.get(VideoSession, part.video_session_id)
        if session is None:
            raise RuntimeError("video session disappeared")
        rows = (
            await db.execute(
                select(VideoProxyPartSource, VideoPart)
                .join(VideoPart, VideoPart.id == VideoProxyPartSource.source_part_id)
                .where(VideoProxyPartSource.proxy_part_id == part_id)
                .order_by(VideoProxyPartSource.position)
            )
        ).all()
        source_parts = [source for _mapping, source in rows]
        if not source_parts:
            raise RuntimeError("proxy part has no source parts")
        return part, session, source_parts


def _ffconcat_quote(path: Path) -> str:
    return str(path).replace("'", "'\\''")


async def build_proxy_part(job_id: int, part_id: uuid.UUID) -> None:
    part, session, source_parts = await load_proxy_inputs(part_id)
    if session.deleted_at_utc is not None:
        raise RuntimeError("video session is in trash")
    if (part.kind or "source") != "proxy":
        raise RuntimeError("not a proxy part")
    profile = part.profile_json or {}
    max_mib = int(profile.get("max_mib") or 1990)
    max_bytes = max_mib * 1024 * 1024
    metadata = session.metadata_json or {}
    root = configured_output_root(str(metadata.get("output_root_key") or "root1")).resolve()
    final_path = safe_child(root, part.relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    source_paths: list[Path] = []
    for source_part in source_parts:
        if (source_part.kind or "source") != "source":
            raise RuntimeError("proxy source is not an original part")
        if source_part.status != "ready":
            raise RuntimeError(f"source Part #{source_part.part_no} is not ready")
        if source_part.local_file_state != "present":
            raise RuntimeError(f"source Part #{source_part.part_no} is not available locally")
        source_path = safe_child(root, source_part.relative_path)
        if not source_path.is_file():
            raise RuntimeError(f"source Part #{source_part.part_no} file is missing")
        if source_part.final_bytes is not None and source_path.stat().st_size != int(source_part.final_bytes):
            raise RuntimeError(f"source Part #{source_part.part_no} size mismatch")
        source_paths.append(source_path)

    for pattern in (f"{final_path.name}.partial-*", f"{final_path.name}.quarantine-*", f"{final_path.name}.concat-*"):
        for stale in final_path.parent.glob(pattern):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
    if final_path.exists():
        quarantine = final_path.with_name(f"{final_path.name}.quarantine-{uuid.uuid4().hex}")
        os.replace(final_path, quarantine)
        fsync_directory(final_path.parent)
        try:
            quarantine.unlink()
        except FileNotFoundError:
            pass

    token = uuid.uuid4().hex
    partial = final_path.with_name(f"{final_path.name}.partial-{token}")
    concat_file = final_path.with_name(f"{final_path.name}.concat-{token}.txt")
    concat_file.write_text("".join(f"file '{_ffconcat_quote(path)}'\n" for path in source_paths), encoding="utf-8")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=60",
        "-c:v", "libx265", "-preset", "medium",
        "-b:v", "4000k", "-maxrate", "4000k", "-bufsize", "8000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
        "-f", "mp4", str(partial),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    communicate_task = asyncio.create_task(process.communicate())
    last_progress = -1
    try:
        while not communicate_task.done():
            written = partial.stat().st_size if partial.exists() else 0
            if await db_cancel_requested(job_id):
                process.terminate()
                await communicate_task
                raise BuildCancelled("cancel requested")
            if gate.requested:
                try:
                    process.send_signal(signal.SIGSTOP)
                    await maybe_wait_gate(job_id, part_id, written)
                finally:
                    if process.returncode is None:
                        process.send_signal(signal.SIGCONT)
            if written != last_progress:
                await progress(job_id, part_id, written, "building")
                last_progress = written
            await asyncio.sleep(0.5)
        _stdout, stderr = await communicate_task
        if process.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg proxy transcode failed ({process.returncode}): {detail[-3000:]}")

        final_bytes = partial.stat().st_size if partial.exists() else 0
        if final_bytes <= 0:
            raise RuntimeError("ffmpeg produced an empty proxy part")
        if final_bytes > max_bytes:
            raise RuntimeError(f"proxy part exceeds max size: {final_bytes} > {max_bytes} bytes ({max_mib} MiB)")

        await progress(job_id, part_id, final_bytes, "verifying")
        digest = hashlib.sha256()
        with partial.open("rb") as handle:
            while True:
                await maybe_wait_gate(job_id, part_id, final_bytes, resume_phase="verifying")
                chunk = handle.read(settings.part_build_chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                await asyncio.sleep(0)
        await maybe_wait_gate(job_id, part_id, final_bytes, resume_phase="verifying")
        os.replace(partial, final_path)
        fsync_directory(final_path.parent)
        await finalize_ready(job_id, part_id, final_bytes, digest.hexdigest(), {})
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
        communicate_task.cancel()
        raise
    finally:
        for temp in (partial, concat_file):
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


async def build_part(job_id: int, part_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        kind = await db.scalar(select(VideoPart.kind).where(VideoPart.id == part_id))
    if kind == "proxy":
        await build_proxy_part(job_id, part_id)
        return
    part, session, pairs = await load_build_inputs(part_id)
    if session.deleted_at_utc is not None:
        raise RuntimeError("video session is in trash")
    run_nos = {segment.video_run_id for _, segment in pairs}
    if len(run_nos) != 1:
        raise RuntimeError("part reservation crosses a run boundary")
    segment_nos = [segment.segment_no for _, segment in pairs]
    if segment_nos != list(range(part.start_segment_no, part.end_segment_no + 1)):
        raise RuntimeError("part reservation is not contiguous")
    if any(segment.storage_state != "archive_ready" for _, segment in pairs):
        raise RuntimeError("part contains a segment that is not archive_ready")

    metadata = session.metadata_json or {}
    root = configured_output_root(str(metadata.get("output_root_key") or "root1")).resolve()
    final_path = safe_child(root, part.relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    for pattern in (f"{final_path.name}.partial-*", f"{final_path.name}.quarantine-*"):
        for stale in final_path.parent.glob(pattern):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

    # A final file without a committed READY row is not trusted as recovery
    # evidence. It may be the residue of a crash between atomic publish and DB
    # finalization. Quarantine + remove it, then rebuild from immutable sources.
    if final_path.exists():
        quarantine = final_path.with_name(f"{final_path.name}.quarantine-{uuid.uuid4().hex}")
        os.replace(final_path, quarantine)
        fsync_directory(final_path.parent)
        try:
            quarantine.unlink()
        except FileNotFoundError:
            pass
        fsync_directory(final_path.parent)

    partial = final_path.with_name(f"{final_path.name}.partial-{uuid.uuid4().hex}")
    part_digest = hashlib.sha256()
    source_hashes: dict[int, str] = {}
    written = 0
    last_report = 0
    loop = asyncio.get_running_loop()
    last_report_at = loop.time()
    heartbeat_interval = max(2.0, settings.part_build_lease_seconds / 3)
    try:
        with partial.open("xb") as destination:
            for reservation, segment in pairs:
                source = safe_child(root, segment.relative_path)
                if not source.exists():
                    raise RuntimeError(f"source segment is missing: {segment.relative_path}")
                actual_size = source.stat().st_size
                if actual_size != int(reservation.expected_bytes):
                    raise RuntimeError(
                        f"source segment size mismatch #{segment.segment_no}: expected={reservation.expected_bytes} actual={actual_size}"
                    )
                source_digest = hashlib.sha256()
                with source.open("rb") as source_handle:
                    while True:
                        await maybe_wait_gate(job_id, part_id, written)
                        chunk = source_handle.read(settings.part_build_chunk_bytes)
                        if not chunk:
                            break
                        destination.write(chunk)
                        source_digest.update(chunk)
                        part_digest.update(chunk)
                        written += len(chunk)
                        now = loop.time()
                        if written - last_report >= 8 * 1024 * 1024 or now - last_report_at >= heartbeat_interval:
                            if await db_cancel_requested(job_id):
                                raise BuildCancelled("cancel requested")
                            await progress(job_id, part_id, written, "building")
                            last_report = written
                            last_report_at = now
                        await asyncio.sleep(0)
                segment_hash = source_digest.hexdigest()
                if segment.sha256 and segment.sha256 != segment_hash:
                    raise RuntimeError(f"source segment sha256 mismatch #{segment.segment_no}")
                source_hashes[int(reservation.id)] = segment_hash
            await maybe_wait_gate(job_id, part_id, written)
            destination.flush()
            os.fsync(destination.fileno())

        if written != int(part.expected_bytes) or partial.stat().st_size != int(part.expected_bytes):
            raise RuntimeError(f"part size mismatch: expected={part.expected_bytes} actual={written}")

        await progress(job_id, part_id, written, "verifying")
        readback = hashlib.sha256()
        verified = 0
        verify_heartbeat_at = loop.time()
        with partial.open("rb") as source_handle:
            while True:
                await maybe_wait_gate(job_id, part_id, written, resume_phase="verifying")
                chunk = source_handle.read(settings.part_build_chunk_bytes)
                if not chunk:
                    break
                readback.update(chunk)
                verified += len(chunk)
                now = loop.time()
                if now - verify_heartbeat_at >= heartbeat_interval:
                    await progress(job_id, part_id, written, "verifying")
                    verify_heartbeat_at = now
                await asyncio.sleep(0)
        if verified != written or readback.hexdigest() != part_digest.hexdigest():
            raise RuntimeError("part read-back sha256 mismatch")

        await maybe_wait_gate(job_id, part_id, written, resume_phase="verifying")
        os.replace(partial, final_path)
        fsync_directory(final_path.parent)
        if final_path.stat().st_size != int(part.expected_bytes):
            raise RuntimeError("published part size mismatch")
        await finalize_ready(job_id, part_id, written, readback.hexdigest(), source_hashes)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


async def finalize_ready(
    job_id: int,
    part_id: uuid.UUID,
    final_bytes: int,
    sha256: str,
    source_hashes: dict[int, str],
) -> None:
    now = now_utc_naive()
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        job = await db.get(VideoPartBuildJob, job_id)
        if part is None or job is None:
            raise RuntimeError("part/job disappeared during finalization")
        if source_hashes:
            reservations = (
                await db.execute(select(VideoPartSegment).where(VideoPartSegment.part_id == part_id))
            ).scalars().all()
            for reservation in reservations:
                if int(reservation.id) in source_hashes:
                    reservation.source_sha256 = source_hashes[int(reservation.id)]
        part.status = "ready"
        part.final_bytes = final_bytes
        part.sha256 = sha256
        part.last_error = None
        part.completed_at_utc = now
        part.updated_at = now
        job.status = "ready"
        job.phase = "ready"
        job.progress_bytes = final_bytes
        job.total_bytes = final_bytes if (part.kind or "source") == "proxy" else int(part.expected_bytes)
        job.last_error = None
        job.completed_at_utc = now
        job.heartbeat_at_utc = now
        job.lease_owner = None
        job.lease_expires_at_utc = None
        await db.commit()


async def mark_failed(job_id: int, part_id: uuid.UUID, exc: BaseException) -> None:
    now = now_utc_naive()
    cancelled = isinstance(exc, BuildCancelled)
    status = "cancelled" if cancelled else "failed"
    message = str(exc)[:4000]
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        job = await db.get(VideoPartBuildJob, job_id)
        if part is not None:
            part.status = status
            part.last_error = message
            part.updated_at = now
        if job is not None:
            job.status = status
            job.phase = status
            job.last_error = message
            job.completed_at_utc = now
            job.lease_owner = None
            job.lease_expires_at_utc = None
            job.heartbeat_at_utc = now
        await db.commit()


async def worker_loop(worker_no: int) -> None:
    while not shutdown.is_set():
        try:
            claimed = await claim_job()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient DB/capture-state failure must not kill the durable queue worker.
            logger.exception("part queue claim failed worker=%s; retrying", worker_no)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1.0)
            except TimeoutError:
                pass
            continue
        if claimed is None:
            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=1.0)
            except TimeoutError:
                pass
            continue
        job_id, part_id = claimed
        await gate.register(job_id, part_id)
        try:
            logger.info("part build start worker=%s job=%s part=%s", worker_no, job_id, part_id)
            await build_part(job_id, part_id)
            logger.info("part build ready worker=%s job=%s part=%s", worker_no, job_id, part_id)
        except asyncio.CancelledError:
            raise
        except (Exception, MemoryError) as exc:
            logger.exception("part build failed worker=%s job=%s part=%s", worker_no, job_id, part_id)
            await mark_failed(job_id, part_id, exc)
        finally:
            await gate.unregister(job_id, part_id)


async def gate_monitor_loop() -> None:
    while not shutdown.is_set():
        if gate.requested and not await capture_active():
            await gate.release()
            wakeup.set()
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=1.0)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_tasks, monitor_task
    shutdown.clear()
    worker_tasks = [asyncio.create_task(worker_loop(index + 1)) for index in range(settings.part_build_workers)]
    monitor_task = asyncio.create_task(gate_monitor_loop())
    wakeup.set()
    try:
        yield
    finally:
        shutdown.set()
        wakeup.set()
        await gate.release()
        tasks = [*worker_tasks, *([monitor_task] if monitor_task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="StreamHub Video Part Builder", lifespan=lifespan)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict:
    async with SessionLocal() as db:
        await db.execute(select(VideoPartBuildJob.id).limit(1))
    roots: dict[str, str] = {"root1": str(configured_output_root("root1"))}
    if settings.video_output_root_2_enabled:
        roots["root2"] = str(configured_output_root("root2"))
    for value in roots.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    workers_alive = sum(1 for task in worker_tasks if not task.done())
    if worker_tasks and workers_alive == 0:
        raise HTTPException(503, "all part-builder workers stopped")
    return {
        "status": "ready",
        "workers": settings.part_build_workers,
        "workers_alive": workers_alive,
        "chunk_bytes": settings.part_build_chunk_bytes,
        "roots": roots,
    }


@app.post("/internal/v1/capture-priority/quiesce", dependencies=[Depends(require_internal_token)])
async def capture_priority_quiesce() -> dict:
    if settings.part_build_allow_during_capture:
        return {"ok": True, "quiesced": False, "allow_during_capture": True}
    await gate.quiesce()
    return {"ok": True, "quiesced": True, "active_jobs": len(gate.active_jobs)}


@app.post("/internal/v1/capture-priority/release", dependencies=[Depends(require_internal_token)])
async def capture_priority_release() -> dict:
    if not await capture_active():
        await gate.release()
        wakeup.set()
        return {"ok": True, "released": True}
    return {"ok": True, "released": False, "reason": "capture_active"}


@app.post("/internal/v1/parts/{part_id}/enqueue", dependencies=[Depends(require_internal_token)])
async def enqueue_part(part_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise HTTPException(404, "part not found")
    wakeup.set()
    return {"ok": True, "part_id": str(part_id)}


@app.post("/internal/v1/parts/{part_id}/retry", dependencies=[Depends(require_internal_token)])
async def retry_part(part_id: uuid.UUID) -> dict:
    if part_id in gate.active_parts:
        raise HTTPException(409, "part is currently building")
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise HTTPException(404, "part not found")
        job = await db.scalar(select(VideoPartBuildJob).where(VideoPartBuildJob.part_id == part_id).with_for_update())
        if job is None:
            raise HTTPException(409, "part build job is missing")
        if part.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "only failed or cancelled parts can be retried")
        part.status = "queued"
        part.last_error = None
        part.final_bytes = None
        part.sha256 = None
        part.completed_at_utc = None
        job.status = "queued"
        job.phase = "queued"
        job.cancel_requested = False
        job.progress_bytes = 0
        job.last_error = None
        job.started_at_utc = None
        job.completed_at_utc = None
        job.lease_owner = None
        job.lease_expires_at_utc = None
        await db.commit()
    await gate.clear_cancel(part_id)
    wakeup.set()
    return {"ok": True, "part_id": str(part_id)}


@app.post("/internal/v1/parts/{part_id}/cancel", dependencies=[Depends(require_internal_token)])
async def cancel_part(part_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise HTTPException(404, "part not found")
        if part.status == "ready":
            raise HTTPException(409, "ready part cannot be cancelled; delete it instead")
        job = await db.scalar(select(VideoPartBuildJob).where(VideoPartBuildJob.part_id == part_id).with_for_update())
        if job is None:
            raise HTTPException(409, "part build job is missing")
        job.cancel_requested = True
        if job.status in {"queued", "waiting_capture_idle"}:
            job.status = "cancelled"
            job.phase = "cancelled"
            job.completed_at_utc = now_utc_naive()
            part.status = "cancelled"
            part.last_error = "cancel requested"
        await db.commit()
    await gate.request_cancel(part_id)
    wakeup.set()
    return {"ok": True, "part_id": str(part_id)}


@app.post("/internal/v1/parts/{part_id}/unlink-local", dependencies=[Depends(require_internal_token)])
async def unlink_local_part(part_id: uuid.UUID) -> dict:
    if part_id in gate.active_parts:
        raise HTTPException(409, "part is currently building")
    quarantine: Path | None = None
    final_path: Path | None = None
    async with SessionLocal() as db:
        part, session = await require_linked_ready_part(db, part_id)
        job = await db.scalar(select(VideoPartBuildJob).where(VideoPartBuildJob.part_id == part_id))
        if job is not None and job.status in BUILDING_JOB_STATUSES:
            raise HTTPException(409, "part build must be idle before unlink")
        metadata = session.metadata_json or {}
        root = configured_output_root(str(metadata.get("output_root_key") or "root1"))
        final_path = safe_child(root, part.relative_path)
        stale_errors: list[str] = []
        for stale in final_path.parent.glob(f"{final_path.name}.unlink-*"):
            error = await unlink_with_retries(stale)
            if error:
                stale_errors.append(f"{stale.name}: {error}")
        if stale_errors:
            part.local_file_state = "cleanup_pending"
            part.local_unlink_error = "; ".join(stale_errors)[:4000]
            await db.commit()
            raise HTTPException(409, "previous part unlink cleanup is still pending")
        if final_path.exists():
            quarantine = final_path.with_name(f"{final_path.name}.unlink-{uuid.uuid4().hex}")
            os.replace(final_path, quarantine)
            fsync_directory(final_path.parent)
        part.local_file_state = "unlinked"
        part.local_unlinked_at_utc = now_utc_naive()
        part.local_unlink_error = None
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            if quarantine is not None and quarantine.exists() and final_path is not None:
                os.replace(quarantine, final_path)
                fsync_directory(final_path.parent)
            raise
    cleanup_error = await unlink_with_retries(quarantine) if quarantine is not None else None
    if cleanup_error:
        async with SessionLocal() as db:
            part = await db.get(VideoPart, part_id)
            if part is not None:
                part.local_file_state = "cleanup_pending"
                part.local_unlink_error = cleanup_error
                await db.commit()
        return {"ok": True, "part_id": str(part_id), "local_file_state": "cleanup_pending", "cleanup_error": cleanup_error}
    return {"ok": True, "part_id": str(part_id), "local_file_state": "unlinked"}


@app.post("/internal/v1/parts/{part_id}/replace-segments", dependencies=[Depends(require_internal_token)])
async def replace_part_segments(part_id: uuid.UUID, payload: SegmentReplacementPayload) -> dict:
    if part_id in gate.active_parts:
        raise HTTPException(409, "part is currently building")
    copy_root, stored_directory = resolve_copy_directory(payload.copy_directory)
    quarantines: list[tuple[Path, Path, int]] = []
    replaced_count = 0
    async with SessionLocal() as db:
        part, session = await require_linked_ready_part(db, part_id)
        if (part.kind or "source") != "source":
            raise HTTPException(409, "proxy parts do not own source segments")
        job = await db.scalar(select(VideoPartBuildJob).where(VideoPartBuildJob.part_id == part_id))
        if job is not None and job.status in BUILDING_JOB_STATUSES:
            raise HTTPException(409, "part build must be idle before replacing segments")
        pairs = (
            await db.execute(
                select(VideoPartSegment, VideoSegment)
                .join(VideoSegment, VideoSegment.id == VideoPartSegment.segment_id)
                .where(VideoPartSegment.part_id == part_id)
                .order_by(VideoPartSegment.segment_no)
            )
        ).all()
        if not pairs:
            raise HTTPException(409, "part has no source segments")

        metadata = session.metadata_json or {}
        output_root = configured_output_root(str(metadata.get("output_root_key") or "root1"))
        verified: list[tuple[VideoSegment, Path]] = []
        for _link, segment in pairs:
            copy_path = (copy_root / segment.file_name).resolve()
            try:
                copy_path.relative_to(copy_root)
            except ValueError as exc:
                raise HTTPException(400, f"invalid segment filename: {segment.file_name}") from exc
            source_path = safe_child(output_root, segment.relative_path)
            if copy_path == source_path.resolve():
                raise HTTPException(409, f"copy directory points to the original segment #{segment.segment_no}; a separate copy is required")
            if not copy_path.is_file():
                raise HTTPException(409, f"copy is missing for segment #{segment.segment_no}: {segment.file_name}")
            actual_size = copy_path.stat().st_size
            if actual_size != int(segment.bytes):
                raise HTTPException(409, f"copy size mismatch for segment #{segment.segment_no}: {actual_size} != {segment.bytes}")
            if not segment.sha256:
                raise HTTPException(409, f"segment #{segment.segment_no} has no SHA-256 and cannot be replaced safely")
            actual_sha256 = await asyncio.to_thread(file_sha256, copy_path)
            if actual_sha256.lower() != segment.sha256.lower():
                raise HTTPException(409, f"copy SHA-256 mismatch for segment #{segment.segment_no}")
            verified.append((segment, copy_path))
        try:
            for segment, copy_path in verified:
                if segment.storage_state == "replaced":
                    continue
                if segment.storage_state != "archive_ready":
                    raise HTTPException(409, f"segment #{segment.segment_no} is in storage state {segment.storage_state}")
                final_path = safe_child(output_root, segment.relative_path)
                for stale in final_path.parent.glob(f"{final_path.name}.replace-*"):
                    error = await unlink_with_retries(stale)
                    if error:
                        raise HTTPException(409, f"previous cleanup pending for segment #{segment.segment_no}: {error}")
                if not final_path.is_file():
                    raise HTTPException(409, f"local segment is missing: {segment.relative_path}")
                quarantine = final_path.with_name(f"{final_path.name}.replace-{uuid.uuid4().hex}")
                os.replace(final_path, quarantine)
                fsync_directory(final_path.parent)
                quarantines.append((quarantine, final_path, segment.id))
                segment.storage_state = "replaced"
                segment.replacement_directory = stored_directory
                segment.replacement_path = f"{stored_directory.rstrip('/\\')}\\{segment.file_name}"
                segment.replaced_at_utc = now_utc_naive()
                segment.archive_last_error = None
                replaced_count += 1
            await db.commit()
        except Exception:
            await db.rollback()
            for quarantine, final_path, _segment_id in reversed(quarantines):
                if quarantine.exists() and not final_path.exists():
                    os.replace(quarantine, final_path)
                    fsync_directory(final_path.parent)
            raise

    cleanup_errors: list[tuple[int, str]] = []
    for quarantine, _final_path, segment_id in quarantines:
        error = await unlink_with_retries(quarantine)
        if error:
            cleanup_errors.append((segment_id, error))
    if cleanup_errors:
        async with SessionLocal() as db:
            for segment_id, error in cleanup_errors:
                segment = await db.get(VideoSegment, segment_id)
                if segment is not None:
                    segment.archive_last_error = f"replacement cleanup pending: {error}"
            await db.commit()
    return {
        "ok": True,
        "part_id": str(part_id),
        "replaced_segments": replaced_count,
        "copy_directory": stored_directory,
        "cleanup_pending": len(cleanup_errors),
    }


@app.delete("/internal/v1/parts/{part_id}", dependencies=[Depends(require_internal_token)])
async def delete_part(part_id: uuid.UUID) -> dict:
    if part_id in gate.active_parts:
        raise HTTPException(409, "part is currently building")
    quarantine: Path | None = None
    final_path: Path | None = None
    async with SessionLocal() as db:
        part = await db.get(VideoPart, part_id)
        if part is None:
            raise HTTPException(404, "part not found")
        session = await db.get(VideoSession, part.video_session_id)
        if session is None:
            raise HTTPException(409, "video session is missing")
        job = await db.scalar(select(VideoPartBuildJob).where(VideoPartBuildJob.part_id == part_id))
        if job is not None and job.status in BUILDING_JOB_STATUSES:
            raise HTTPException(409, "part build must be idle before delete")
        metadata = session.metadata_json or {}
        root = configured_output_root(str(metadata.get("output_root_key") or "root1"))
        final_path = safe_child(root, part.relative_path)
        if final_path.exists():
            quarantine = final_path.with_name(f"{final_path.name}.quarantine-{uuid.uuid4().hex}")
            os.replace(final_path, quarantine)
            fsync_directory(final_path.parent)
        for pattern in (f"{final_path.name}.partial-*", f"{final_path.name}.quarantine-*"):
            for stale in final_path.parent.glob(pattern):
                try:
                    stale.unlink()
                except FileNotFoundError:
                    pass
        try:
            await db.execute(delete(VideoPart).where(VideoPart.id == part_id))
            await db.commit()
        except Exception:
            await db.rollback()
            if quarantine is not None and quarantine.exists() and final_path is not None:
                os.replace(quarantine, final_path)
                fsync_directory(final_path.parent)
            raise
    if quarantine is not None:
        try:
            quarantine.unlink()
        except FileNotFoundError:
            pass
    await gate.clear_cancel(part_id)
    return {"ok": True, "part_id": str(part_id)}
