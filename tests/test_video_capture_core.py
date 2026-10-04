from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_video_commands_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps/video_recorder/app/commands.py"
    spec = importlib.util.spec_from_file_location("streamhub_test_video_commands", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_video_recorder_commands_are_lossless_and_resume_only_vod():
    module = load_video_commands_module()
    vod_command = module.build_streamlink_command(
        media_type="vod",
        source_url="https://www.twitch.tv/videos/123",
        quality="best",
        resume_source_offset_ms=12_345,
        stream_timeout_seconds=90,
    )
    assert vod_command[-2:] == ["https://www.twitch.tv/videos/123", "best"]
    offset_index = vod_command.index("--hls-start-offset")
    assert vod_command[offset_index + 1] == "12.345"

    live_command = module.build_streamlink_command(
        media_type="live",
        source_url="https://www.twitch.tv/example",
        quality="best",
        resume_source_offset_ms=12_345,
        stream_timeout_seconds=90,
    )
    assert "--hls-start-offset" not in live_command

    ffmpeg_command = module.build_ffmpeg_command(
        segments_dir=Path("/spool/session/segments"),
        runs_dir=Path("/spool/session/runs"),
        run_no=2,
        start_number=17,
        segment_seconds=10,
    )
    codec_index = ffmpeg_command.index("-c")
    assert ffmpeg_command[codec_index + 1] == "copy"
    flags_index = ffmpeg_command.index("-hls_flags")
    assert "temp_file" in ffmpeg_command[flags_index + 1]
    assert ffmpeg_command[-1].endswith("run_000002.m3u8")
    assert any("seg_%06d.ts" in value for value in ffmpeg_command)

    assert module.segment_no_from_name("seg_000017.ts") == 17
    assert module.segment_no_from_name("seg_000018.ts.tmp") == 18
    assert module.segment_no_from_name("other.ts") is None


def test_video_metadata_resolution_can_use_app_credentials_without_chat_oauth():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/twitch_adapter/app/main.py").read_text()
    assert '"grant_type": "client_credentials"' in source
    assert "async def load_metadata_access_token()" in source
    resolver = source.split('async def resolve_twitch_metadata(payload: MetadataResolveRequest)', 1)[1]
    assert "metadata_token = await load_metadata_access_token()" in resolver
    assert "load_runtime_twitch_auth()" not in resolver.split("@app.post(\"/internal/v1/vod-fetches\"", 1)[0]


def test_stage2_video_schema_is_additive_and_does_not_touch_chat_tables():
    root = Path(__file__).resolve().parents[1]
    source = (root / "migrations/versions/0003_video_capture_core.py").read_text()
    assert 'down_revision = "0002_event_domain"' in source
    for table in ("video_sessions", "video_runs", "video_segments", "video_gaps"):
        assert f'"{table}"' in source
    upgrade = source.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    assert 'drop_table("sessions")' not in upgrade
    assert 'drop_table("chat_messages")' not in upgrade
    assert 'alter_column("sessions"' not in upgrade


def test_live_video_segments_preserve_original_stream_clock_for_chat_sync():
    root = Path(__file__).resolve().parents[1]
    recorder = (root / "apps/video_recorder/app/main.py").read_text(encoding="utf-8")
    commands = (root / "apps/video_recorder/app/commands.py").read_text(encoding="utf-8")

    assert "program_date_time" in commands
    assert "def playlist_segment_program_times(" in recorder
    assert 'value.startswith("#EXT-X-PROGRAM-DATE-TIME:")' in recorder
    assert "event = await db.get(MediaEvent, session.event_id)" in recorder
    assert "source_offset_ms(program_time, source_origin)" in recorder
    assert "source_media_start_ms=source_start" in recorder
    assert "source_media_end_ms=source_end" in recorder
    assert "run_source_start + timeline_start_ms - run_timeline_start" in recorder
