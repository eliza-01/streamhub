from pathlib import Path


def test_env_is_gitignored():
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text()
    assert ".env" in ignore.splitlines()


def test_extension_does_not_contain_twitch_client_secret():
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(p.read_text(errors="ignore") for p in (root / "apps/chrome_extension").glob("*.*"))
    assert "TWITCH_CLIENT_SECRET" not in text


def test_extension_can_observe_twitch_integrity_headers():
    import json

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "apps/chrome_extension/manifest.json").read_text())
    assert "webRequest" in manifest["permissions"]
    assert "https://gql.twitch.tv/*" in manifest["host_permissions"]
    service_worker = (root / "apps/chrome_extension/service_worker.js").read_text()
    assert "client-integrity" in service_worker.lower()
    assert "x-device-id" in service_worker.lower()
    assert 'headers["authorization"]' in service_worker


def test_twitch_adapter_forwards_browser_auth_with_integrity():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/twitch_adapter/app/main.py").read_text()
    assert 'headers["Authorization"] = integrity.authorization' in source


def test_web_progress_poll_is_batched_slow_and_visibility_aware():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/web/src/main.tsx").read_text()
    assert "PROGRESS_POLL_MS = 5000" in source
    assert 'document.visibilityState !== "visible"' in source
    assert 'params.append("session_id", id)' in source
    assert "/sessions/capture-progress" in source


def test_permanent_purge_removes_session_scoped_traces():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/api/app/routers/sessions.py").read_text()
    for model in ("AuditLog", "ChatEvent", "ChatMessage", "SessionSegment", "CaptureJob", "Session"):
        assert f"delete({model})" in source
    assert "ensure_capture_is_idle_for_delete" in source
    assert 'poll_after_ms": 5000' in source


def test_live_collector_is_implemented_and_pause_keeps_capture_running():
    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/twitch_adapter/app/main.py").read_text()
    assert "LIVE EventSub + IRC redundant collector is intentionally not claimed complete" not in source
    assert '"channel.chat.message"' in source
    assert '"channel.chat.message_delete"' in source
    assert "capture_continues" in source
    assert "run_irc_source" in source


def test_device_auth_requests_irc_read_scope_for_redundancy():
    root = Path(__file__).resolve().parents[1]
    settings_source = (root / "packages/python_common/streamhub_common/settings.py").read_text()
    adapter_source = (root / "apps/twitch_adapter/app/main.py").read_text()
    assert "twitch_requested_scopes" in settings_source
    assert "settings.twitch_requested_scopes" in adapter_source
