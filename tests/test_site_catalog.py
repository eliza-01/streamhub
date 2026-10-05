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


def test_site_watch_exposes_persisted_chat_and_syncs_it_through_source_timeline():
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")

    assert 'ChatMessage.timeline_offset_ms >= start' in route
    assert 'ChatMessage.timeline_offset_ms <= end' in route
    assert '@router.get("/events/{event_id}/playback-timeline")' in route
    assert '"source_start_ms"' in route
    assert '"timeline_start_ms"' in route
    assert '"mapping_quality"' in route
    assert '"primary_chat_session_id"' in route
    assert '"chat_message_count"' in route
    assert 'function SiteReplayChat(' in web
    assert '/chat/messages?' in web
    assert '/playback-timeline' in web
    assert 'playerToSourceMs(currentPlayer, ranges)' in web
    assert 'sourceToPlayerMs(message.timeline_offset_ms, ranges)' in web
    assert '(message.player_offset_ms ?? Number.POSITIVE_INFINITY) <= currentMs + 100' in web
    assert 'className="site-replay-chat-time"' not in web
    assert '{message.player_offset_ms != null ? fmtMs(message.player_offset_ms) : "—"}' in web
    assert 'message.player_offset_ms ?? message.timeline_offset_ms' not in web
    assert 'video.currentTime = Math.max(0, message.timeline_offset_ms / 1000);' not in web


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


def test_public_site_v1_has_streamvault_shell_and_chat_user_context_menu():
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert '@router.get("/events/{event_id}/chat/stats")' in route
    assert '@router.get("/events/{event_id}/chat/user-summary")' in route
    assert 'ChatMessage.chatter_external_id == external_id' in route
    assert 'func.count(ChatMessage.id)' in route
    assert '"most_active": _chat_user_payload(most_active)' in route
    assert '"least_active": _chat_user_payload(least_active)' in route
    assert '"chatter_external_id": row.chatter_external_id' in route

    assert 'function StreamVaultHeader(' in web
    assert 'function SiteChatStats(' in web
    assert '/chat/stats`' in web
    assert 'Последний стрим' in web
    assert 'PREVIEW ${siteHeroFrame + 1}' in web
    assert 'Чат записи' in web
    assert 'Все сообщения' in web
    assert 'Посмотреть профиль' in web
    assert 'Отметить в комментарии' in web
    assert '<span>Отметить в комментарии</span><b>@</b>' not in web
    assert '/chat/user-summary?' in web
    assert 'site-inline-spinner' in web
    assert 'className="site-chat-author"' in web

    assert '.site-chat-user-menu' in css
    assert 'position: fixed;' in css
    assert '.streamvault-watch-layout' in css
    assert '.streamvault-recording-grid' in css


def test_site_chat_user_history_hotfixes_and_twitch_profile_action():
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert '@router.get("/events/{event_id}/chat/user-messages")' in route
    assert '@router.get("/events/{event_id}/chat/user-events")' in route
    assert '"messages": [_chat_message_payload(row) for row in page]' in route
    assert 'SiteEventPublication.event_id == MediaEvent.id' in route
    assert 'ChatMessage.session_id.in_(session_ids)' in route
    assert '"message_count": message_count' in route
    assert '"badges": _chat_badges(profile_row) if profile_row else []' in route
    assert 'ChatMessage.chatter_external_id.is_(None)' in route

    assert 'function SiteChatUserContext(' in web
    assert '/chat/user-messages?' in web
    assert '/chat/user-events?' in web
    assert '>Этот стрим</button>' in web
    assert '<span>Другие</span>' in web
    assert 'site-chat-history-tab-count' in web
    assert 'siteChatOtherEventsCache' in web
    assert 'siteChatOtherEventsCache.set(otherEventsCacheKey, items)' in web
    assert 'site-chat-history-other-heading' not in web
    assert '["#000", "#000000", "black", "rgb(0,0,0)", "rgba(0,0,0,1)"]' in web
    assert 'return "#8b949e"' in web
    assert 'Сообщений:' in web
    assert 'Посмотреть профиль Twitch' in web
    assert 'function TwitchIcon()' in web
    assert 'onClick={(event) => openStatsUser(event, stats?.most_active)}' in web
    assert 'onClick={(event) => openStatsUser(event, stats?.least_active)}' in web

    assert 'onWheel={handleChatWheel}' in web
    assert 'onTouchMove={handleChatTouchMove}' in web
    assert 'const movingUp = currentTop < previousTop - 0.5' in web
    assert 'pointerScrollIntentRef.current && movingUp && distance > 4' in web
    assert 'if (distance <= 2) {' in web
    assert 'pausedWindowStartIdRef.current = null' in web
    assert 'onPointerDown={handleChatPointerDown}' in web
    assert 'overflow-anchor: none' in css
    assert 'pausedWindowStartIdRef' in web
    assert 'if (followingRef.current) void loadWindow(false)' in web
    assert 'if (following) return eligible.slice(-500)' in web
    assert 'node.scrollTo({ top: node.scrollHeight, behavior: "smooth" })' in web
    assert 'node.scrollTop = node.scrollHeight' in web
    assert 'className="site-chat-follow"' in web
    assert '!following && visible.length > 0' in web
    assert 'Сообщения появятся через <strong>{nextMessageCountdown}</strong>' in web
    assert 'fetchFirstFutureMessage' in web
    assert 'page_size: "1"' in web
    assert 'video.currentTime = Math.max(0, nextMessagePlayerMs / 1000)' in web
    assert 'В этой точке таймлайна сообщений пока нет.' not in web
    assert 'button.site-replay-chat-empty.is-seekable' in css
    assert 'Клик по нику — профиль и история сообщений' not in web
    assert 'className="event-chevron"' not in web
    assert 'grid-template-columns: 24px minmax(0, 1fr) auto' not in css
    assert '.site-chat-author { display: inline;' in css and 'cursor: pointer;' in css
    assert 'site-chat-type' not in web
    assert 'Срез всей записи' not in web
    assert 'Нажми на пользователя, чтобы открыть профиль и историю сообщений' not in web
    assert 'streamvault-chat-stat-user-name' in web

    assert '.site-chat-history-modal' in css
    assert '.site-chat-history-events' in css
    assert '.site-chat-history-event' in css
    assert '.site-twitch-icon' in css
    assert 'left: 50%; bottom: 12px; transform: translateX(-50%)' in css
    assert 'const onWaiting = () => markLoadingSoon(320)' in web
    assert 'video.addEventListener("timeupdate", markProgressing)' in web


def test_site_user_comments_are_persisted_authenticated_and_rate_limited():
    migration = _read("migrations/versions/0017_site_comments.py")
    models = _read("packages/python_common/streamhub_common/models.py")
    route = _read("apps/api/app/routers/site.py")
    auth = _read("apps/api/app/routers/user_auth.py")
    web = _read("apps/web/src/main.tsx")

    assert 'down_revision = "0016_users_telegram_registration"' in migration
    assert '"site_comments"' in migration
    assert 'class SiteComment(Base):' in models
    assert '@router.get("/events/{event_id}/comments")' in route
    assert '@router.post("/events/{event_id}/comments", status_code=201)' in route
    assert 'Depends(require_current_user)' in route
    assert '.with_for_update()' in route
    assert 'status.HTTP_429_TOO_MANY_REQUESTS' in route
    assert '"retry_after_seconds": retry_after' in route
    assert 'async def require_current_user(' in auth
    assert '/comments?page_size=100' in web
    assert '/comments`' in web
    assert 'Следующий комментарий через' in web
    assert 'UI v1 · пользовательские комментарии подключим после утверждения интерфейса.' not in web


def test_site_admin_assets_migration_keeps_source_title_and_persistent_asset_metadata():
    migration = _read("migrations/versions/0011_site_admin_assets.py")
    models = _read("packages/python_common/streamhub_common/models.py")
    settings = _read("packages/python_common/streamhub_common/settings.py")
    compose = _read("docker-compose.yml")
    requirements = _read("requirements.txt")

    assert 'down_revision = "0010_site_catalog"' in migration
    assert '"display_title"' in migration
    assert 'UPDATE media_events SET display_title = title' in migration
    assert '"site_event_assets"' in migration
    assert 'class SiteEventAsset(Base):' in models
    assert 'display_title: Mapped[str | None]' in models
    assert 'SITE_ASSET_ROOT' in settings
    assert 'streamhub_site_assets:/site_assets' in compose
    assert 'streamhub_site_assets:' in compose
    assert 'Pillow>=' in requirements


def test_site_admin_api_and_ui_manage_display_title_cover_and_four_manual_frames():
    route = _read("apps/api/app/routers/site.py")
    assets = _read("apps/api/app/site_assets.py")
    domain = _read("apps/api/app/event_domain.py")
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert 'display_title=title' in domain
    assert 'return event.display_title or event.title' in route
    assert '"source_title": event.title' in route
    assert '@router.get("/admin/events")' in route
    assert '@router.put("/admin/events/{event_id}")' in route
    assert '@router.put("/admin/events/{event_id}/assets/{slot}")' in route
    assert '@router.get("/assets/{event_id}/{slot}/{sha256}.webp")' in route
    assert 'event requires one cover and exactly four preview frames before publishing' in route
    assert 'SITE_ASSET_SLOTS = ("cover", "frame_1", "frame_2", "frame_3", "frame_4")' in assets
    assert 'os.replace(temporary, destination)' in assets
    assert 'format="WEBP"' in assets

    assert '>StreamHub</button>' in web
    assert '>Админка</button>' in web
    assert 'function SiteAdminPanel(' in web
    assert 'Исходное название:' in web
    assert 'Отображаемое название' in web
    assert '["frame_4", "Кадр 4"]' in web
    assert 'src={latest.assets?.cover?.url}' in web
    assert 'src={latest.assets?.frames?.[siteHeroFrame]?.url}' in web
    assert 'onPublish={() => void openSitePublisher()}' not in web
    assert '.site-admin-modal' in css
    assert '.streamvault-artwork.has-image' in css
    assert '"Файл или Ctrl+V"' not in web


def test_site_timecodes_use_event_owned_relational_storage_and_public_payloads():
    migration = _read("migrations/versions/0012_site_event_timecodes.py")
    models = _read("packages/python_common/streamhub_common/models.py")
    route = _read("apps/api/app/routers/site.py")

    assert 'down_revision = "0011_site_admin_assets"' in migration
    assert '"site_event_timecodes"' in migration
    assert 'sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE")' in migration
    assert '"ix_site_event_timecodes_event_offset"' in migration
    assert 'class SiteEventTimecode(Base):' in models
    assert 'UniqueConstraint("event_id", "position", name="uq_site_event_timecodes_position")' in models
    assert '@router.put("/admin/events/{event_id}/timecodes")' in route
    assert 'delete(SiteEventTimecode).where(SiteEventTimecode.event_id == event_id)' in route
    assert 'action="site_timecodes_replace"' in route
    assert '"timecodes": [_site_timecode_payload(row) for row in timecodes.get(event.id, [])]' in route


def test_site_admin_collapses_events_and_edits_timecodes_without_embedding_a_player():
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert '<details className="site-admin-event"' in web
    assert '<summary className="site-admin-event-summary">' in web
    assert '<span>Статус</span>' in web
    assert '<span>Дата</span>' in web
    assert '<span>Категории</span>' in web
    assert '<span>Название</span>' in web
    assert '<span>Изображения</span>' in web
    assert 'Добавить таймкоды' in web
    assert 'placeholder="00:00:00"' in web
    assert 'placeholder="Например: Начало матча"' in web
    assert 'fetch(`${API}/api/v1/site/admin/events/${eventId}/timecodes`' in web
    assert 'Время указывается относительно публичного видеоплеера.' in web
    assert '.site-admin-event-summary' in css
    assert '.site-admin-timecode-row' in css


def test_public_event_renders_clickable_timecodes_and_active_section_heading_without_count():
    web = _read("apps/web/src/main.tsx")
    css = _read("apps/web/src/styles.css")

    assert 'function SiteEventTimecodes(' in web
    assert '<SiteEventTimecodes items={selectedSiteEvent.timecodes || []} videoRef={siteVideoRef} />' in web
    assert 'video.currentTime = Math.max(0, item.offset_ms / 1000)' in web
    assert '{siteCategory === "all" ? "Все видео"' in web
    assert '<span>{siteEvents.length.toLocaleString("ru-RU")}</span>' not in web
    assert '.streamvault-timecodes-list button' in css
    assert '.streamvault-hero > h1, .streamvault-recordings h2 { margin: 0; color: var(--sv-orange-2);' in css


def test_site_visibility_is_persistent_and_public_routes_require_visible_active_storage():
    migration = _read("migrations/versions/0013_site_publication_visibility.py")
    models = _read("packages/python_common/streamhub_common/models.py")
    route = _read("apps/api/app/routers/site.py")
    web = _read("apps/web/src/main.tsx")

    assert 'down_revision = "0012_site_event_timecodes"' in migration
    assert '"hidden_at_utc"' in migration
    assert 'hidden_at_utc: Mapped[datetime | None]' in models
    assert '@router.put("/admin/events/{event_id}/visibility")' in route
    assert 'action="site_hide" if payload.hidden else "site_show"' in route
    assert 'SiteEventPublication.hidden_at_utc.is_(None)' in route
    assert 'MediaEvent.id.in_(storage_ids)' in route
    assert 'async def _require_public_event' in route
    assert 'await _require_public_event(db, event_id)' in route
    assert 'delete(EventCategory).where(EventCategory.event_id == event_id)' not in route.split(
        '@router.delete("/admin/events/{event_id}")', 1
    )[1]
    assert 'Скрыть публикацию' in web
    assert 'Показать публикацию' in web
    assert '/visibility`' in web
    assert 'event.hidden ? "Скрыто" : "Опубликовано"' in web
