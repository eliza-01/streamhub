from __future__ import annotations

import hashlib
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest


def load_storage_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps/video_recorder/app/storage.py"
    spec = importlib.util.spec_from_file_location("streamhub_test_video_storage", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_archive_layout_is_uuid_stable():
    module = load_storage_module()
    event_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    session_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    relative = module.archive_segment_relative_path(event_id, session_id, "seg_000123.ts")
    assert str(relative) == (
        "twitch/events/11111111-1111-1111-1111-111111111111/"
        "video/22222222-2222-2222-2222-222222222222/segments/seg_000123.ts"
    )


def test_atomic_archive_copy_keeps_spool_until_caller_releases_it(tmp_path: Path):
    module = load_storage_module()
    source = tmp_path / "spool" / "seg_000001.ts"
    final = tmp_path / "recordings" / "segments" / source.name
    source.parent.mkdir(parents=True)
    payload = (b"streamhub-mpeg-ts" * 8192) + b"tail"
    source.write_bytes(payload)

    result = module.atomic_copy_verified(source, final, expected_bytes=len(payload), min_free_bytes=0)

    assert source.read_bytes() == payload
    assert final.read_bytes() == payload
    assert result.bytes == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert not list(final.parent.glob("*.partial-*"))


def test_archive_copy_is_idempotent_after_publish_before_db_commit(tmp_path: Path):
    module = load_storage_module()
    source = tmp_path / "spool" / "seg_000002.ts"
    final = tmp_path / "recordings" / "segments" / source.name
    source.parent.mkdir(parents=True)
    final.parent.mkdir(parents=True)
    payload = b"already-published" * 2048
    source.write_bytes(payload)
    final.write_bytes(payload)

    result = module.atomic_copy_verified(source, final, expected_bytes=len(payload), min_free_bytes=0)

    assert source.exists()
    assert final.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()


def test_archive_copy_rejects_changed_spool_bytes(tmp_path: Path):
    module = load_storage_module()
    source = tmp_path / "spool" / "seg_000003.ts"
    final = tmp_path / "recordings" / "segments" / source.name
    source.parent.mkdir(parents=True)
    source.write_bytes(b"short")

    with pytest.raises(module.ArchiveCopyError, match="spool size mismatch"):
        module.atomic_copy_verified(source, final, expected_bytes=999, min_free_bytes=0)

    assert source.exists()
    assert not final.exists()


def test_stage4_migration_and_compose_are_additive():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0004_video_spool_archive.py").read_text()
    compose = (root / "docker-compose.yml").read_text()
    settings = (root / "packages/python_common/streamhub_common/settings.py").read_text()

    assert 'down_revision = "0003_video_capture_core"' in migration
    assert '"archive_attempts"' in migration
    assert '"archive_last_error"' in migration
    assert '"archived_at_utc"' in migration
    assert 'drop_table("sessions")' not in migration
    assert 'drop_table("chat_messages")' not in migration
    assert "VIDEO_OUTPUT_ROOT_1_HOST:?" in compose
    assert "VIDEO_OUTPUT_ROOT_1_HOST:-./output" not in compose
    assert "target: /outputs/root1" in compose
    assert "VIDEO_OUTPUT_ROOT_2_HOST:?" in compose
    assert "target: /outputs/root2" in compose
    assert "VIDEO_OUTPUT_ROOT_3_HOST" not in compose
    assert 'alias="VIDEO_OUTPUT_ROOT_1"' in settings
    assert 'alias="VIDEO_OUTPUT_ROOT_2"' in settings
    assert 'alias="VIDEO_STORAGE_COPY_WORKERS"' in settings


def test_spool_release_happens_only_after_batch_archive_ready_commit_in_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/video_recorder/app/main.py").read_text()
    function = source.split("async def process_archive_batch", 1)[1].split(
        "async def storage_worker_loop", 1
    )[0]

    commit_at = function.index("await db.commit()")
    cleanup_at = function.index("cleanup_spool_copy_after_commit")
    assert commit_at < cleanup_at
    assert 'row.storage_state = "archive_ready"' in function
    assert "for candidate in candidates" in function


def test_stage5_output_settings_and_batch_size_are_wired():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0005_storage_output_batches.py").read_text()
    recorder = (root / "apps/video_recorder/app/main.py").read_text()
    api = (root / "apps/api/app/routers/storage.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()

    assert 'down_revision = "0004_video_spool_archive"' in migration
    assert '"storage_output_settings"' in migration
    assert "video_archive_batch_segments" in recorder
    assert "claim_archive_batch" in recorder
    assert 'prefix="/api/v1/storage"' in api
    assert "output-settings" in api
    assert "Хранилище" in web
    assert "batch_segments" in web


def test_output_tree_migration_is_verified_and_keeps_source_until_caller_commits(tmp_path: Path):
    module = load_storage_module()
    source = tmp_path / "root1" / "session"
    destination = tmp_path / "root2" / "session"
    (source / "segments").mkdir(parents=True)
    (source / "logs").mkdir(parents=True)
    (source / "segments" / "seg_000001.ts").write_bytes(b"mpeg-ts" * 4096)
    (source / "session.json").write_text("{\"ok\": true}\n", encoding="utf-8")
    (source / "logs" / "recorder.log").write_text("done\n", encoding="utf-8")

    copied = module.atomic_copy_tree_verified(source, destination, min_free_bytes=0)

    assert copied == module.directory_size_bytes(source)
    assert source.exists()
    assert destination.exists()
    assert module.directory_size_bytes(destination) == copied
    assert (destination / "segments" / "seg_000001.ts").read_bytes() == (source / "segments" / "seg_000001.ts").read_bytes()
    assert not list(destination.parent.glob(".*.migrate-*"))
