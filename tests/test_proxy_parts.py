from pathlib import Path

from streamhub_common.telegram_names import canonical_part_identity, filenames_equivalent


def test_proxy_parts_schema_and_profile_are_wired():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0022_proxy_parts.py").read_text()
    models = (root / "packages/python_common/streamhub_common/models.py").read_text()
    api = (root / "apps/api/app/routers/video.py").read_text()
    builder = (root / "apps/video_part_builder/app/main.py").read_text()
    dockerfile = (root / "apps/video_part_builder/Dockerfile").read_text()
    playback = (root / "apps/api/app/telegram/playback.py").read_text()
    site = (root / "apps/api/app/routers/site.py").read_text()

    assert 'revision = "0022_proxy_parts"' in migration
    assert 'down_revision = "0021_site_watch_room_chat"' in migration
    assert 'video_proxy_part_sources' in migration
    assert 'kind: Mapped[str]' in models
    assert 'class VideoProxyPartSource' in models
    assert '/proxy-parts/plan' in api
    assert '/proxy-parts/build-all' in api
    assert 'video_bitrate_kbps": 4000' in api
    assert 'audio_bitrate_kbps": 160' in api
    assert 'proxy_parts/{file_name}' in api
    assert 'scale=1920:1080' in builder
    assert '"fps=60"' not in builder  # the fps filter is part of the combined filter string
    assert 'fps=60' in builder
    assert '"4000k"' in builder
    assert '"160k"' in builder
    assert 'libx265' in builder
    assert 'apt-get install -y --no-install-recommends ffmpeg' in dockerfile
    assert 'VideoPart.kind == "source"' in playback
    assert 'VideoPart.kind == "source"' in site


def test_proxy_telegram_filename_has_stable_identity():
    original = "vpx_0123456789abcdef0123456789abcdef__part0003__p0007-p0011.mp4"
    normalized = "vpx_0123456789abcdef0123456789abcdef_part_3_p7-p11.mp4"
    assert canonical_part_identity(original) == ("0123456789abcdef0123456789abcdef", 3, 7, 11)
    assert filenames_equivalent(original, normalized)
