from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

COPY_CHUNK_BYTES = 1024 * 1024


class ArchiveCopyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveCopyResult:
    bytes: int
    sha256: str


def archive_session_relative_path(event_id: uuid.UUID, session_id: uuid.UUID) -> PurePosixPath:
    return PurePosixPath("twitch") / "events" / str(event_id) / "video" / str(session_id)


def archive_segment_relative_path(event_id: uuid.UUID, session_id: uuid.UUID, file_name: str) -> PurePosixPath:
    return archive_session_relative_path(event_id, session_id) / "segments" / file_name


def safe_archive_session_root(recordings_root: Path, event_id: uuid.UUID, session_id: uuid.UUID) -> Path:
    root = recordings_root.resolve()
    candidate = (root / Path(*archive_session_relative_path(event_id, session_id).parts)).resolve()
    candidate.relative_to(root)
    return candidate


def ensure_archive_dirs(recordings_root: Path, event_id: uuid.UUID, session_id: uuid.UUID) -> tuple[Path, Path, Path, Path]:
    root = safe_archive_session_root(recordings_root, event_id, session_id)
    segments = root / "segments"
    runs = root / "runs"
    parts = root / "parts"
    logs = root / "logs"
    for path in (segments, runs, parts, logs):
        path.mkdir(parents=True, exist_ok=True)
    return segments, runs, parts, logs


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sha256_file(path: Path, *, chunk_bytes: int = COPY_CHUNK_BYTES) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_stale_partials(final_path: Path) -> None:
    pattern = f"{final_path.name}.partial-*"
    for candidate in final_path.parent.glob(pattern):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _quarantine_invalid_final(final_path: Path) -> None:
    if not final_path.exists():
        return
    quarantine = final_path.with_name(f"{final_path.name}.quarantine-{uuid.uuid4().hex}")
    os.replace(final_path, quarantine)
    _fsync_directory(final_path.parent)


def atomic_copy_verified(
    source: Path,
    final_path: Path,
    *,
    expected_bytes: int,
    min_free_bytes: int = 0,
    chunk_bytes: int = COPY_CHUNK_BYTES,
) -> ArchiveCopyResult:
    """Publish one immutable segment without deleting the spool source.

    The caller owns the DB transaction and the post-commit spool deletion. This
    function is intentionally idempotent so a crash after os.replace() but before
    the DB commit can be recovered by validating the already-published final file.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_partials(final_path)

    source_exists = source.exists()
    if source_exists:
        source_size = source.stat().st_size
        if source_size != expected_bytes:
            raise ArchiveCopyError(
                f"spool size mismatch for {source.name}: expected={expected_bytes} actual={source_size}"
            )

    if final_path.exists():
        final_size = final_path.stat().st_size
        if final_size == expected_bytes:
            final_hash = sha256_file(final_path, chunk_bytes=chunk_bytes)
            if source_exists:
                source_hash = sha256_file(source, chunk_bytes=chunk_bytes)
                if source_hash != final_hash:
                    _quarantine_invalid_final(final_path)
                else:
                    return ArchiveCopyResult(bytes=expected_bytes, sha256=final_hash)
            else:
                return ArchiveCopyResult(bytes=expected_bytes, sha256=final_hash)
        elif source_exists:
            _quarantine_invalid_final(final_path)
        else:
            raise ArchiveCopyError(
                f"archive size mismatch and spool source is missing for {final_path.name}: "
                f"expected={expected_bytes} actual={final_size}"
            )

    if not source_exists:
        raise ArchiveCopyError(f"spool source is missing for {source.name}")

    free_bytes = shutil.disk_usage(final_path.parent).free
    required_free = expected_bytes + max(0, min_free_bytes)
    if free_bytes < required_free:
        raise ArchiveCopyError(
            f"archive free space below safety reserve: free={free_bytes} required={required_free}"
        )

    partial = final_path.with_name(f"{final_path.name}.partial-{uuid.uuid4().hex}")
    source_digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as src, partial.open("xb") as dst:
            while True:
                chunk = src.read(chunk_bytes)
                if not chunk:
                    break
                dst.write(chunk)
                source_digest.update(chunk)
                copied += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())

        if copied != expected_bytes or partial.stat().st_size != expected_bytes:
            raise ArchiveCopyError(
                f"archive copy size mismatch for {source.name}: expected={expected_bytes} copied={copied}"
            )

        readback_hash = sha256_file(partial, chunk_bytes=chunk_bytes)
        write_hash = source_digest.hexdigest()
        if readback_hash != write_hash:
            raise ArchiveCopyError(f"archive read-back hash mismatch for {source.name}")

        os.replace(partial, final_path)
        _fsync_directory(final_path.parent)
        if final_path.stat().st_size != expected_bytes:
            raise ArchiveCopyError(f"published archive size mismatch for {source.name}")
        return ArchiveCopyResult(bytes=expected_bytes, sha256=readback_hash)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial-{uuid.uuid4().hex}")
    try:
        with partial.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
        _fsync_directory(path.parent)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def directory_is_writable(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".streamhub-write-probe-{uuid.uuid4().hex}"
    try:
        with probe.open("xb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
