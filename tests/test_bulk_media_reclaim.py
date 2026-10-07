from pathlib import Path


def test_bulk_media_reclaim_is_wired_and_not_output_root_limited():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0023_bulk_media_reclaim.py").read_text()
    models = (root / "packages/python_common/streamhub_common/models.py").read_text()
    api = (root / "apps/api/app/routers/video.py").read_text()
    builder = (root / "apps/video_part_builder/app/main.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()

    assert 'revision = "0023_bulk_media_reclaim"' in migration
    assert 'down_revision = "0022_proxy_parts"' in migration
    assert "external_copy_directory" in models
    assert "/reclaim-manifest" in api
    assert "/reclaim-parts" in api
    assert "/reclaim-segments" in api
    assert "bulk reclaim must include every local ready part" in builder
    assert "bulk reclaim must include every local archive_ready segment" in builder
    assert "showDirectoryPicker" in web
    assert "sha256File" in web
    assert "Проверить и удалить все source Parts" in web
    assert "Проверить копии и удалить все segments" in web
    assert "Каталог должен быть внутри подключённого VIDEO_OUTPUT_ROOT" not in web
    assert "Unlink Part" not in web
    assert "Replace segments" not in web


def test_video_manager_shows_event_title_and_proxy_builder():
    root = Path(__file__).resolve().parents[1]
    web = (root / "apps/web/src/main.tsx").read_text()
    assert 'className="video-manager-event-title"' in web
    assert "visibleVideoEventTitle(selectedVideo, eventTitleMode)" in web
    assert "МОНТАЖ · ПОНИЖЕННОЕ КАЧЕСТВО" in web
    assert "Собрать proxy_parts · HEVC 1080p60" in web
