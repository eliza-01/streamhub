from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import delete, or_, select, update

from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import (
    VideoPart,
    VideoPartBuildJob,
    VideoPartSegment,
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


async def build_part(job_id: int, part_id: uuid.UUID) -> None:
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
        job.total_bytes = int(part.expected_bytes)
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
