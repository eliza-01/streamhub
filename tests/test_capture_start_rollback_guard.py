from pathlib import Path


def test_capture_start_does_not_touch_expired_event_after_mode_rollback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/api/app/routers/capture.py").read_text()
    start_block = source.split('@router.post("/capture/start")', 1)[1].split('@router.get("/capture/progress")', 1)[0]
    video_block = source.split("async def start_video_for_event", 1)[1].split('@router.post("/capture/start")', 1)[0]

    assert "event_id = event.id" in start_block
    assert '"event": event_snapshot' in start_block
    assert "active_chat_for_event(db, event.id)" not in start_block
    assert "db.get(MediaEvent, event.id)" not in start_block
    assert "active_session_id = active.id" in video_block
    assert "str(active.id)" not in video_block


def test_recorder_transport_failure_is_returned_as_video_failure():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/api/app/routers/video.py").read_text()
    recorder_block = source.split("async def recorder_post", 1)[1].split("async def stop_video_capture_row", 1)[0]

    assert "except httpx.HTTPError as exc:" in recorder_block
    assert "video-recorder unavailable" in recorder_block

def test_video_session_is_flushed_before_fk_audit_row():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/api/app/routers/capture.py").read_text()
    video_block = source.split("async def start_video_for_event", 1)[1].split('@router.post("/capture/start")', 1)[0]

    session_add = video_block.index("db.add(session)")
    parent_flush = video_block.index("await db.flush()", session_add)
    audit_add = video_block.index("AuditLog(", parent_flush)

    assert session_add < parent_flush < audit_add

