from __future__ import annotations

import importlib.util
from pathlib import Path


def load_telegram_names():
    root = Path(__file__).resolve().parents[1]
    path = root / "packages/python_common/streamhub_common/telegram_names.py"
    spec = importlib.util.spec_from_file_location("streamhub_test_telegram_names", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_part_name_survives_telegram_punctuation_normalization():
    names = load_telegram_names()
    expected = "vp_207cbe6fb58f4b35a18d3767a71683e2__part0001__s000001-s000011.ts"
    normalized = "vp_207cbe6f_b58f_4b35_a18d_3767a71683e2_part0001_s000001_s000011.ts"
    assert names.canonical_part_identity(expected) == (
        "207cbe6fb58f4b35a18d3767a71683e2",
        1,
        1,
        11,
    )
    assert names.filenames_equivalent(expected, normalized)
    assert not names.filenames_equivalent(expected, normalized.replace("s000011", "s000012"))


def test_legacy_part_name_is_still_understood_for_reconciliation():
    names = load_telegram_names()
    legacy = "vp_207cbe6f-b58f-4b35-a18d-3767a71683e2__part_000001__seg_000001-000011.ts"
    normalized = "vp_207cbe6f_b58f_4b35_a18d_3767a71683e2_part_000001_seg_000001_000011.ts"
    assert names.canonical_part_identity(legacy) == (
        "207cbe6fb58f4b35a18d3767a71683e2",
        1,
        1,
        11,
    )
    assert names.filenames_equivalent(legacy, normalized)


def test_telegram_storage_is_read_only_user_session_pipeline():
    root = Path(__file__).resolve().parents[1]
    storage = (root / "apps/api/app/telegram/storage.py").read_text()
    runtime = (root / "apps/api/app/telegram/runtime.py").read_text()
    routes = (root / "apps/api/app/routers/telegram.py").read_text()
    compose = (root / "docker-compose.yml").read_text()

    assert "TelegramClient" in storage
    assert "is_user_authorized" in storage
    assert "iter_messages" in storage
    assert "iter_download" in storage
    assert "send_message" not in storage
    assert "send_file" not in storage
    assert "upload_file" not in storage
    assert "bot_token" not in storage.lower()
    assert "telegram-readonly-sync" in runtime
    assert '@router.post("/sync")' in routes
    assert "./telegram_state:/telegram_state" in compose


def test_telegram_schema_binds_part_to_channel_message_and_backfills_offsets():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0009_telegram_storage.py").read_text()
    models = (root / "packages/python_common/streamhub_common/models.py").read_text()
    video = (root / "apps/api/app/routers/video.py").read_text()

    assert 'down_revision = "0008_video_parts"' in migration
    for table in ("telegram_channel_state", "telegram_channel_files", "telegram_video_part_bindings"):
        assert f'"{table}"' in migration
    assert 'op.add_column(' in migration
    assert '"part_offset_bytes"' in migration
    assert "ORDER BY part_id,segment_no,id" in migration
    assert 'name="uq_telegram_binding_message"' in migration
    assert 'name="fk_telegram_binding_file"' in migration
    assert "class TelegramVideoPartBinding" in models
    assert "part_offset_bytes: Mapped[int]" in models
    assert "part_offset_bytes=part_offset_bytes" in video
    assert "part_offset_bytes += int(segment.bytes)" in video


def test_playback_reads_only_the_required_part_byte_range():
    root = Path(__file__).resolve().parents[1]
    playback = (root / "apps/api/app/telegram/playback.py").read_text()
    routes = (root / "apps/api/app/routers/playback.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()

    assert 'VideoPartSegment.part_offset_bytes' in playback
    assert 'offset=int(row["part_offset_bytes"]) + requested.start' in playback
    assert "iter_document_range" in playback
    assert '@router.get("/segments/{part_id}/{segment_no}.ts")' in routes
    assert '@router.get("/parts/{part_id}.ts")' in routes
    assert "Telegram sync" in web
    assert "telegramBindings[part.id]" in web
    assert "TelegramVideoPlayer" in web


def test_telegram_playback_refreshes_expired_file_reference_and_resumes_range():
    root = Path(__file__).resolve().parents[1]
    storage = (root / "apps/api/app/telegram/storage.py").read_text()

    assert "FileReferenceExpiredError" in storage
    assert "refresh: bool = False" in storage
    assert "refresh=refreshed_reference" in storage
    assert "current_offset += len(data)" in storage
    assert "Telegram file reference expired again after refresh" in storage
