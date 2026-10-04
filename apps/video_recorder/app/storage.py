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


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def directory_size_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in _iter_files(root))


def _verify_tree_equal(source_root: Path, destination_root: Path) -> int:
    source_files = _iter_files(source_root)
    destination_files = _iter_files(destination_root)
    source_rel = [path.relative_to(source_root) for path in source_files]
    destination_rel = [path.relative_to(destination_root) for path in destination_files]
    if source_rel != destination_rel:
        raise ArchiveCopyError("destination tree does not contain the same files as source")

    total = 0
    for relative in source_rel:
        source = source_root / relative
        destination = destination_root / relative
        source_size = source.stat().st_size
        destination_size = destination.stat().st_size
        if source_size != destination_size:
            raise ArchiveCopyError(f"migration size mismatch for {relative}")
        if sha256_file(source) != sha256_file(destination):
            raise ArchiveCopyError(f"migration hash mismatch for {relative}")
        total += source_size
    return total


def atomic_copy_tree_verified(
    source_root: Path,
    final_root: Path,
    *,
    min_free_bytes: int = 0,
) -> int:
    """Copy one immutable session tree across output roots and publish atomically.

    The source is never removed here. The caller must first commit the DB switch
    to the destination root and only then delete the old tree.
    """
    source_root = source_root.resolve()
    if not source_root.exists():
        raise ArchiveCopyError(f"migration source does not exist: {source_root}")
    final_root.parent.mkdir(parents=True, exist_ok=True)

    try:
        if final_root.exists() and os.path.samefile(source_root, final_root):
            return directory_size_bytes(source_root)
    except OSError:
        pass

    if final_root.exists():
        return _verify_tree_equal(source_root, final_root)

    source_bytes = directory_size_bytes(source_root)
    free_bytes = shutil.disk_usage(final_root.parent).free
    required_free = source_bytes + max(0, min_free_bytes)
    if free_bytes < required_free:
        raise ArchiveCopyError(
            f"destination free space below safety reserve: free={free_bytes} required={required_free}"
        )

    partial = final_root.with_name(f".{final_root.name}.migrate-{uuid.uuid4().hex}")
    try:
        partial.mkdir(parents=False, exist_ok=False)
        for source in _iter_files(source_root):
            relative = source.relative_to(source_root)
            destination = partial / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_digest = hashlib.sha256()
            with source.open("rb") as src, destination.open("xb") as dst:
                while True:
                    chunk = src.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    dst.write(chunk)
                    source_digest.update(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            if source.stat().st_size != destination.stat().st_size:
                raise ArchiveCopyError(f"migration size mismatch for {relative}")
            if source_digest.hexdigest() != sha256_file(destination):
                raise ArchiveCopyError(f"migration read-back hash mismatch for {relative}")
            _fsync_directory(destination.parent)

        _verify_tree_equal(source_root, partial)
        os.replace(partial, final_root)
        _fsync_directory(final_root.parent)
        return source_bytes
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)


def _move_tree_files(source_root: Path, destination_root: Path) -> None:
    """Move every file individually, preserving the relative tree.

    Windows/Docker Desktop can refuse renaming a non-empty bind-mounted
    directory even when no process inside either container has an open file
    descriptor for it.  Moving the files themselves on the same filesystem is
    still atomic and gives purge a safe fallback without copying video bytes.

    The operation is transactional at filesystem level: if any file move
    fails, already-moved files are put back before the exception is raised.
    """
    moved: list[tuple[Path, Path]] = []
    destination_root.mkdir(parents=False, exist_ok=False)
    try:
        for source in _iter_files(source_root):
            relative = source.relative_to(source_root)
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ArchiveCopyError(f"purge quarantine destination already exists: {destination}")
            os.replace(source, destination)
            moved.append((source, destination))
            _fsync_directory(destination.parent)

        leftovers = _iter_files(source_root)
        if leftovers:
            raise ArchiveCopyError(
                f"purge quarantine left {len(leftovers)} source files behind: {source_root}"
            )

        # A Windows bind mount may keep the now-empty source directory locked.
        # That is harmless: all durable artifacts are already in quarantine.
        try:
            shutil.rmtree(source_root)
        except OSError:
            pass
        _fsync_directory(source_root.parent)
    except Exception:
        for source, destination in reversed(moved):
            if not destination.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, source)
            _fsync_directory(source.parent)
        shutil.rmtree(destination_root, ignore_errors=True)
        raise


def _restore_tree_files(quarantine: Path, path: Path) -> None:
    moved: list[tuple[Path, Path]] = []
    path.mkdir(parents=True, exist_ok=True)
    try:
        for source in _iter_files(quarantine):
            relative = source.relative_to(quarantine)
            destination = path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ArchiveCopyError(f"cannot restore purge file because destination exists: {destination}")
            os.replace(source, destination)
            moved.append((source, destination))
            _fsync_directory(destination.parent)
        shutil.rmtree(quarantine)
        _fsync_directory(path.parent)
    except Exception:
        for source, destination in reversed(moved):
            if not destination.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, source)
            _fsync_directory(source.parent)
        raise


def quarantine_directory(path: Path, token: str) -> bool:
    """Quarantine a directory without copying its payload.

    Fast path is a single same-filesystem directory rename.  If Windows bind
    mount semantics reject that rename with PermissionError, fall back to
    same-filesystem atomic moves of the individual files into a sibling
    quarantine directory.
    """
    if not path.exists():
        return False
    quarantine = path.with_name(f"{path.name}.purge-{token}")
    if quarantine.exists():
        raise ArchiveCopyError(f"purge quarantine already exists: {quarantine}")
    try:
        os.replace(path, quarantine)
    except PermissionError:
        _move_tree_files(path, quarantine)
    _fsync_directory(path.parent)
    return True


def restore_quarantined_directory(path: Path, token: str) -> None:
    quarantine = path.with_name(f"{path.name}.purge-{token}")
    if not quarantine.exists():
        return

    # Whole-directory quarantine can be restored by one rename.  The
    # file-by-file Windows fallback can leave an empty locked source directory,
    # so in that case merge the quarantined files back into it.
    if not path.exists():
        try:
            os.replace(quarantine, path)
            _fsync_directory(path.parent)
            return
        except PermissionError:
            pass
    _restore_tree_files(quarantine, path)


def purge_quarantine_details(path: Path) -> tuple[Path, uuid.UUID, str] | None:
    """Return (original_path, session_id, token) for a purge quarantine directory."""
    marker = ".purge-"
    if marker not in path.name:
        return None
    session_text, token = path.name.rsplit(marker, 1)
    if len(token) != 32:
        return None
    try:
        int(token, 16)
        session_id = uuid.UUID(session_text)
    except (ValueError, TypeError):
        return None
    return path.with_name(session_text), session_id, token.lower()


def find_purge_quarantines(root: Path) -> list[tuple[Path, Path, uuid.UUID, str]]:
    """Find StreamHub purge quarantine directories below a storage root.

    Only names matching ``<session UUID>.purge-<32 hex token>`` are returned,
    so unrelated directories containing ``.purge-`` are never touched.
    """
    if not root.exists():
        return []
    found: list[tuple[Path, Path, uuid.UUID, str]] = []
    for candidate in root.rglob("*.purge-*"):
        if not candidate.is_dir():
            continue
        details = purge_quarantine_details(candidate)
        if details is None:
            continue
        original, session_id, token = details
        found.append((candidate, original, session_id, token))
    return found


def delete_quarantined_directory(path: Path, token: str) -> None:
    quarantine = path.with_name(f"{path.name}.purge-{token}")
    if quarantine.exists():
        shutil.rmtree(quarantine)
    # The Windows fallback may intentionally have left only empty directories
    # at the original location.  They no longer contain referenced artifacts.
    if path.exists() and not _iter_files(path):
        shutil.rmtree(path, ignore_errors=True)
    _fsync_directory(path.parent)
