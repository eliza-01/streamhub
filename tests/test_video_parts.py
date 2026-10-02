from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path


def load_part_core():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps/video_part_builder/app/core.py"
    spec = importlib.util.spec_from_file_location("streamhub_test_part_core", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_part_schema_is_additive_and_reserves_each_segment_once():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0008_video_parts.py").read_text()
    assert 'down_revision = "0007_event_session_trash"' in migration
    for table in ("video_parts", "video_part_segments", "video_part_build_jobs"):
        assert f'"{table}"' in migration
    assert 'name="uq_video_part_segment_reservation"' in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    assert 'drop_table("video_segments")' not in upgrade
    assert 'drop_table("video_sessions")' not in upgrade


def test_part_filename_is_deterministic_and_path_guard_rejects_escape(tmp_path: Path):
    core = load_part_core()
    session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert core.part_file_name(session_id, 7, 12, 34) == (
        "vp_12345678123456781234567812345678__part0007__s000012-s000034.ts"
    )
    safe = core.safe_child(tmp_path, "streamhub/twitch/events/e/video/s/parts/x.ts")
    assert safe.is_relative_to(tmp_path.resolve())
    try:
        core.safe_child(tmp_path, "../escape.ts")
    except ValueError:
        pass
    else:
        raise AssertionError("safe_child accepted path traversal")


def test_part_api_supports_plan_reservation_queue_and_actions():
    root = Path(__file__).resolve().parents[1]
    video = (root / "apps/api/app/routers/video.py").read_text()
    assert '@router.post("/video-sessions/{session_id}/parts/plan")' in video
    assert '@router.post("/video-sessions/{session_id}/parts")' in video
    assert '@router.get("/video-sessions/{session_id}/parts")' in video
    assert '@router.post("/video-parts/{part_id}/retry")' in video
    assert '@router.post("/video-parts/{part_id}/cancel")' in video
    assert '@router.delete("/video-parts/{part_id}")' in video
    assert ".with_for_update()" in video
    assert "VideoPartSegment(" in video
    assert 'segment.storage_state != "archive_ready"' in video
    assert 'segment.integrity_state != "hashed"' in video
    assert '"run_no": int(run_no)' in video


def test_builder_uses_capture_gate_chunked_hash_readback_and_atomic_publish():
    root = Path(__file__).resolve().parents[1]
    builder = (root / "apps/video_part_builder/app/main.py").read_text()
    capture = (root / "apps/api/app/routers/capture.py").read_text()
    compose = (root / "docker-compose.yml").read_text()

    assert 'settings.part_build_chunk_bytes' in builder
    assert 'hashlib.sha256()' in builder
    assert 'destination.flush()' in builder
    assert 'os.fsync(destination.fileno())' in builder
    assert 'readback.hexdigest() != part_digest.hexdigest()' in builder
    assert 'os.replace(partial, final_path)' in builder
    assert '/internal/v1/capture-priority/quiesce' in builder
    assert 'await gate.before_io' in builder

    start = capture.split("async def start_video_for_event", 1)[1].split("@router.post", 1)[0]
    assert start.index('part_builder_post("/internal/v1/capture-priority/quiesce"') < start.index('recorder_post(')

    assert "video-part-builder:" in compose
    builder_compose = compose.split("  video-part-builder:", 1)[1].split("\n  api:", 1)[0]
    assert "ports:" not in builder_compose
    assert "target: /outputs/root1" in builder_compose
    assert "target: /outputs/root2" in builder_compose


def test_video_manager_exposes_parts_build_queue_and_target_mib():
    root = Path(__file__).resolve().parents[1]
    web = (root / "apps/web/src/main.tsx").read_text()
    assert ">Video Manager</button>" in web
    assert "BUILD PART" in web
    assert "PARTS / BUILD QUEUE" in web
    assert "Target MiB" in web
    assert "Manual range" in web
    assert "waiting_capture_idle" in web
    assert "Copy path" in web


def test_parts_integrate_with_delete_and_storage_migration_guards():
    root = Path(__file__).resolve().parents[1]
    video = (root / "apps/api/app/routers/video.py").read_text()
    events = (root / "apps/api/app/routers/events.py").read_text()
    storage_api = (root / "apps/api/app/routers/storage.py").read_text()
    recorder = (root / "apps/video_recorder/app/main.py").read_text()

    assert "PART_ACTIVE_JOB_STATUSES" in video
    assert "video session has an active part build" in video
    assert "await db.execute(delete(VideoPart).where(VideoPart.video_session_id == session_id))" in video
    assert "delete_video_db_rows" in events

    assert "sessions_with_active_part_jobs" in storage_api
    assert "VideoPart.status == \"ready\"" in storage_api
    assert "segment_bytes + ready_part_bytes.get(row.id, 0)" in storage_api
    assert "pending part builds" in storage_api

    assert "video session has pending/active part build during storage migration" in recorder
    assert "archive_bytes = segment_bytes + ready_part_bytes" in recorder
    assert "return True, archive_bytes" in recorder
