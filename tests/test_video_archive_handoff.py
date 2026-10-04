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


def test_purge_quarantine_falls_back_to_file_moves_when_directory_rename_is_locked(tmp_path: Path, monkeypatch):
    module = load_storage_module()
    session_root = tmp_path / "output" / "session"
    (session_root / "segments").mkdir(parents=True)
    (session_root / "logs").mkdir(parents=True)
    first = session_root / "segments" / "seg_000001.ts"
    second = session_root / "logs" / "recorder.log"
    first.write_bytes(b"mpeg-ts" * 4096)
    second.write_text("done\n", encoding="utf-8")
    token = "locked-directory"
    quarantine = session_root.with_name(f"{session_root.name}.purge-{token}")
    real_replace = module.os.replace

    def replace_with_locked_session_directory(source, destination):
        if Path(source) == session_root and Path(destination) == quarantine:
            raise PermissionError(13, "directory locked")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace_with_locked_session_directory)

    assert module.quarantine_directory(session_root, token) is True
    assert not module._iter_files(session_root)
    assert (quarantine / "segments" / "seg_000001.ts").read_bytes() == b"mpeg-ts" * 4096
    assert (quarantine / "logs" / "recorder.log").read_text(encoding="utf-8") == "done\n"

    module.restore_quarantined_directory(session_root, token)

    assert first.read_bytes() == b"mpeg-ts" * 4096
    assert second.read_text(encoding="utf-8") == "done\n"
    assert not quarantine.exists()


def test_purge_file_move_fallback_rolls_back_if_one_artifact_is_locked(tmp_path: Path, monkeypatch):
    module = load_storage_module()
    session_root = tmp_path / "output" / "session"
    (session_root / "segments").mkdir(parents=True)
    first = session_root / "segments" / "seg_000001.ts"
    second = session_root / "segments" / "seg_000002.ts"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    token = "partial-failure"
    quarantine = session_root.with_name(f"{session_root.name}.purge-{token}")
    real_replace = module.os.replace

    def replace_with_one_locked_file(source, destination):
        source = Path(source)
        destination = Path(destination)
        if source == session_root and destination == quarantine:
            raise PermissionError(13, "directory locked")
        if source == second:
            raise PermissionError(13, "file locked")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace_with_one_locked_file)

    with pytest.raises(PermissionError):
        module.quarantine_directory(session_root, token)

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert not quarantine.exists()


def test_storage_worker_claim_failures_are_retried_and_supervised_in_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/video_recorder/app/main.py").read_text()
    worker = source.split("async def storage_worker_loop", 1)[1].split(
        "async def storage_worker_supervisor", 1
    )[0]
    supervisor = source.split("async def storage_worker_supervisor", 1)[1].split(
        "async def recover_archive_handoff", 1
    )[0]

    assert "candidates = await claim_archive_batch()" in worker
    assert "except Exception as exc:" in worker
    assert "storage worker claim failed" in worker
    assert "await storage_worker_retry_delay()" in worker
    assert "await storage_worker_loop(worker_no)" in supervisor
    assert "supervisor will restart it" in supervisor


def test_recorder_health_exposes_archive_workers_and_backlog_in_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/video_recorder/app/main.py").read_text()
    health = source.split('@app.get("/health/ready")', 1)[1].split(
        '@app.post("/internal/v1/video-sessions"', 1
    )[0]

    assert '"storage_workers": worker_status' in health
    assert '"archive_backlog": archive_backlog' in health
    assert '"dead_workers": dead_workers' in health
    assert 'VideoSegment.storage_state.in_({"spool", "copying"})' in health


def test_purge_quarantine_discovery_only_accepts_session_uuid_and_hex_token(tmp_path: Path):
    module = load_storage_module()
    session_id = uuid.UUID("dd968802-b40a-494d-ae8f-0d532fe5d655")
    token = "080b6becff654e5e8d084d86225253a2"
    original = tmp_path / str(session_id)
    quarantine = tmp_path / f"{session_id}.purge-{token}"
    quarantine.mkdir()
    (tmp_path / "not-a-session.purge-080b6becff654e5e8d084d86225253a2").mkdir()
    (tmp_path / f"{session_id}.purge-nothex").mkdir()

    details = module.purge_quarantine_details(quarantine)
    assert details == (original, session_id, token)
    found = module.find_purge_quarantines(tmp_path)
    assert found == [(quarantine, original, session_id, token)]


def test_video_recorder_retries_finalize_and_reconciles_purge_quarantines_on_startup():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/video_recorder/app/main.py").read_text()

    assert "delete_quarantined_directory_with_retry" in source
    assert "reconcile_purge_quarantines(restore_referenced=True)" in source
    assert "purge_quarantine_reaper_loop" in source
    assert 'name="video-purge-reaper"' in source
