from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceSegment:
    segment_id: int
    segment_no: int
    path: Path
    expected_bytes: int
    expected_sha256: str | None


@dataclass(frozen=True)
class PartBuildResult:
    final_bytes: int
    sha256: str
    source_sha256: dict[int, str]


def part_file_name(session_id: uuid.UUID, part_no: int, start_segment_no: int, end_segment_no: int) -> str:
    compact = session_id.hex
    return f"vp_{compact}__part{part_no:04d}__s{start_segment_no:06d}-s{end_segment_no:06d}.ts"


def safe_child(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    candidate = (root / Path(relative_path)).resolve()
    candidate.relative_to(root)
    return candidate


def fsync_directory(path: Path) -> None:
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


def sha256_file(path: Path, *, chunk_bytes: int = 524288) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
