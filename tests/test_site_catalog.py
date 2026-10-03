from pathlib import Path


def _read(path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / path).read_text(encoding="utf-8")


def test_site_catalog_migration_seeds_normalized_categories_and_explicit_publications():
    migration = _read("migrations/versions/0010_site_catalog.py")
    models = _read("packages/python_common/streamhub_common/models.py")

    assert 'down_revision = "0009_telegram_storage"' in migration
    assert '"films", "Фильмы"' in migration
    assert '"shows", "Шоу"' in migration
    assert '"games", "Игры"' in migration
    assert '"fncs", "FNCS"' in migration
    assert '"chatroulette", "Чатрулетка"' in migration
    assert '"site_event_publications"' in migration
    assert '"event_categories"' in migration
    assert 'class ContentCategory(Base):' in models
    assert 'class SiteEventPublication(Base):' in models
    assert 'class EventCategory(Base):' in models


def test_site_publish_api_is_event_centric_and_requires_telegram_backed_storage():
    route = _read("apps/api/app/routers/site.py")
    main = _read("apps/api/app/main.py")

    assert 'router = APIRouter(prefix="/api/v1/site"' in route
    assert '@router.get("/feed")' in route
    assert '@router.get("/events/{event_id}/chat/messages")' in route
    assert '@router.get("/admin/available-events")' in route
    assert '@router.post("/admin/events"' in route
    assert '@router.put("/admin/events/{event_id}/categories")' in route
    assert '@router.delete("/admin/events/{event_id}")' in route
    assert 'TelegramVideoPartBinding' in route
    assert 'VideoPart.status == "ready"' in route
    assert 'event has no Telegram-linked ready video parts' in route
    assert 'app.include_router(site.router)' in main


def test_site_frontend_fetches_categories_and_events_instead_of_hardcoding_content():
    web = _read("apps/web/src/main.tsx")

    assert '>Сайт</button>' in web
    assert '>+ Добавить событие</button>' in web
    assert 'fetch(`${API}/api/v1/site/feed?' in web
    assert 'fetch(`${API}/api/v1/site/admin/available-events`' in web
    assert 'fetch(`${API}/api/v1/site/categories`' in web
    assert 'siteCategories.map((category)' in web
    assert 'siteAvailableEvents.map((event)' in web
    assert 'FNCS' not in web
    assert 'Чатрулетка' not in web


def test_site_feed_reuses_telegram_hls_playback_without_double_api_prefix():
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")

    assert 'f"/api/v1/playback/video-sessions/{session.id}/index.m3u8"' in route
    assert 'const source = apiUrl(playlistUrl);' in web
    assert 'playlistUrl={selectedSiteEvent.playback_url}' in web
    assert 'playlistUrl={`${API}${selectedSiteEvent.playback_url}`}' not in web


def test_site_watch_exposes_persisted_chat_and_syncs_it_to_video_timeline():
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")

    assert 'ChatMessage.timeline_offset_ms >= start' in route
    assert 'ChatMessage.timeline_offset_ms <= end' in route
    assert '"primary_chat_session_id"' in route
    assert '"chat_message_count"' in route
    assert 'function SiteReplayChat(' in web
    assert '/chat/messages?' in web
    assert 'message.timeline_offset_ms <= currentMs + 100' in web
    assert 'video.currentTime = Math.max(0, message.timeline_offset_ms / 1000);' in web


def test_site_chat_reconstructs_vod_text_and_exposes_rich_presentation_fields():
    route = _read("apps/api/app/routers/site.py")
    adapter = _read("apps/twitch_adapter/app/main.py")
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert 'return "".join(str(item.get("text") or "") for item in _chat_fragments(row))' in route
    assert '"color": _chat_color(row)' in route
    assert '"badges": _chat_badges(row)' in route
    assert '"fragments": _chat_fragments(row)' in route
    assert '"reply": row.reply_json' in route
    assert '"bits": row.bits' in route

    assert 'color=message.get("userColor") or message.get("color") or commenter.get("color")' in adapter
    assert '"".join(' in adapter
    assert 'str(fragment.get("text") or "") for fragment in fragments if isinstance(fragment, dict)' in adapter

    assert 'function chatBadges(' in web
    assert 'function ChatMessageFragments(' in web
    assert 'static-cdn.jtvnw.net/emoticons/v2/' in web
    assert 'chatReplySummary(message.reply)' in web
    assert 'message.bits.toLocaleString("ru-RU")' in web
    assert 'site-chat-badge' in css
    assert 'site-chat-emote' in css


def test_video_manager_does_not_auto_mount_telegram_player_and_site_badges_use_twitch_assets():
    route = _read("apps/api/app/routers/site.py")
    adapter = _read("apps/twitch_adapter/app/main.py")
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert 'Telegram playback ready · segments' in web
    assert '<TelegramVideoPlayer playlistUrl={playbackStatus.playlist_url} />' not in web
    assert '@router.get("/events/{event_id}/chat/badges")' in route
    assert '/internal/v1/chat/badges/{broadcaster_id}' in adapter
    assert '"/chat/badges/global"' in adapter
    assert '"/chat/badges"' in adapter
    assert 'image_url_1x' in adapter
    assert 'fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/badges`)' in web
    assert 'site-chat-badge-image' in web
    assert 'site-chat-badge-image' in css
    assert 'badge.imageUrl ? (' in web
    assert '<span className="site-chat-badge"' in web
