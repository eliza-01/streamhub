from pathlib import Path


def test_delete_and_storage_migration_schema_is_additive():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0006_delete_and_storage_migration.py").read_text()
    assert 'down_revision = "0005_storage_output_batches"' in migration
    assert '"deletion_group_id"' in migration
    assert '"storage_migration_jobs"' in migration
    assert 'drop_table("sessions")' not in migration
    assert 'drop_table("video_sessions")' not in migration


def test_event_and_video_delete_routes_are_exposed():
    root = Path(__file__).resolve().parents[1]
    events = (root / "apps/api/app/routers/events.py").read_text()
    video = (root / "apps/api/app/routers/video.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()

    assert '@router.delete("/events/{event_id}")' in events
    assert '@router.post("/events/{event_id}/restore")' in events
    assert '@router.delete("/deleted/events/{event_id}", status_code=202)' in events
    assert '@router.delete("/video-sessions/{session_id}")' in video
    assert '@router.post("/video-sessions/{session_id}/restore")' in video
    assert '@router.delete("/deleted/video-sessions/{session_id}")' in video
    assert "В корзину все sessions" in web
    assert "Восстановить все sessions" in web
    assert "Удалить из корзины навсегда" in web
    assert "VIDEO SESSIONS В КОРЗИНЕ" in web


def test_event_trash_is_session_scoped_and_video_purge_uses_quarantine():
    root = Path(__file__).resolve().parents[1]
    events = (root / "apps/api/app/routers/events.py").read_text()
    video = (root / "apps/api/app/routers/video.py").read_text()
    recorder = (root / "apps/video_recorder/app/main.py").read_text()

    assert "deletion_group_id = uuid.uuid4()" in events
    assert 'action="event_sessions_soft_delete"' in events
    assert "event.deleted_at_utc = None" in events
    assert "Session.deleted_at_utc.is_not(None)" in events
    assert "VideoSession.deleted_at_utc.is_not(None)" in events
    assert "quarantine_video_for_purge" in video
    assert "/purge-quarantine" in recorder
    assert "wait_for_archive_copy_idle" in recorder
    assert "VideoSegment.storage_state == \"copying\"" in recorder
    assert "quarantine_directory_with_retry" in recorder
    assert "/internal/v1/video-purge/restore" in recorder
    assert "/internal/v1/video-purge/finalize" in recorder


def test_storage_migration_is_wired_from_api_to_recorder_and_web():
    root = Path(__file__).resolve().parents[1]
    api = (root / "apps/api/app/routers/storage.py").read_text()
    recorder = (root / "apps/video_recorder/app/main.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()
    compose = (root / "docker-compose.yml").read_text()

    assert '@router.post("/migrations")' in api
    assert '@router.post("/migrations/{job_id}/retry")' in api
    assert "storage_migration_loop" in recorder
    assert "atomic_copy_tree_verified" in recorder
    assert 'metadata["output_root_key"] = destination_root_key' in recorder
    migration_function = recorder.split("async def migrate_storage_session", 1)[1].split(
        "async def process_storage_migration_job", 1
    )[0]
    assert migration_function.index("await db.commit()") < migration_function.rindex("shutil.rmtree")
    assert "OUTPUT MIGRATION" in web
    assert "Мигрировать output" in web
    assert "VIDEO_OUTPUT_ROOT_2_HOST:?" in compose
    assert "target: /outputs/root2" in compose


def test_canonical_event_identity_is_not_reactivated_or_destroyed_by_session_trash():
    root = Path(__file__).resolve().parents[1]
    domain = (root / "apps/api/app/event_domain.py").read_text()
    sessions = (root / "apps/api/app/routers/sessions.py").read_text()
    capture = (root / "apps/api/app/routers/capture.py").read_text()
    events = (root / "apps/api/app/routers/events.py").read_text()
    video = (root / "apps/api/app/routers/video.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()

    assert "reactivate_media_event_for_capture" not in domain
    assert "reactivate_media_event_for_capture" not in sessions
    assert "reactivate_media_event_for_capture" not in capture
    assert "MediaEvent.deleted_at_utc.is_(None)" not in events.split('@router.get("/events")', 1)[1].split('@router.get("/deleted/events")', 1)[0]
    assert "MediaEvent.deleted_at_utc.is_not(None)" not in events.split('@router.get("/deleted/events")', 1)[1].split('@router.get("/events/{event_id}")', 1)[0]
    assert "delete(MediaEvent)" not in sessions
    assert "delete(MediaEvent)" not in video
    assert "delete(MediaEvent)" not in events
    assert "Этот же уникальный Event может одновременно быть в Events" in web


def test_event_identity_session_trash_data_migration_is_additive():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/versions/0007_event_session_trash.py").read_text()
    assert 'down_revision = "0006_delete_storage_migration"' in migration
    assert "UPDATE media_events" in migration
    assert "SET deleted_at_utc = NULL, deletion_group_id = NULL" in migration
    assert "UPDATE sessions" not in migration
    assert "UPDATE video_sessions" not in migration


def test_permanent_event_purge_cleans_site_editorial_state_only_when_event_is_orphaned():
    root = Path(__file__).resolve().parents[1]
    events = (root / "apps/api/app/routers/events.py").read_text()

    assert "async def _clear_orphaned_site_editorial_state" in events
    assert 'return [], {"cleared": False, "reason": "event_has_remaining_sessions"}' in events
    assert "delete(SiteEventTimecode).where(SiteEventTimecode.event_id == event.id)" in events
    assert "delete(EventCategory).where(EventCategory.event_id == event.id)" in events
    assert "delete(SiteEventAsset).where(SiteEventAsset.event_id == event.id)" in events
    assert "delete(SiteEventPublication).where(SiteEventPublication.event_id == event.id)" in events
    assert "event.display_title = event.title" in events
    assert 'action="site_editorial_cleanup"' in events
    assert "remove_site_asset_file(settings, storage_key)" in events
    assert '"site_asset_files_cleanup_requested"' in events


def test_event_trash_permanent_delete_uses_durable_queue():
    root = Path(__file__).resolve().parents[1]
    events = (root / "apps/api/app/routers/events.py").read_text()
    models = (root / "packages/python_common/streamhub_common/models.py").read_text()
    migration = (root / "migrations/versions/0014_event_purge_queue.py").read_text()
    web = (root / "apps/web/src/main.tsx").read_text()
    api_main = (root / "apps/api/app/main.py").read_text()

    assert "class EventPurgeJob(Base)" in models
    assert 'down_revision = "0013_site_publication_visibility"' in migration
    assert 'status="queued"' in events
    assert 'EventPurgeJob.status == "queued"' in events
    assert '.order_by(EventPurgeJob.id.asc())' in events
    assert '@router.get("/deleted/event-purge-jobs")' in events
    assert '@router.delete("/deleted/events/{event_id}", status_code=202)' in events
    assert '@router.post("/deleted/event-purge-jobs/{job_id}/retry")' in events
    assert "event_purge_queue_runtime.start()" in api_main
    assert "ОЧЕРЕДЬ УДАЛЕНИЯ" in web
    assert 'job.status === "queued" || job.status === "running"' in web
