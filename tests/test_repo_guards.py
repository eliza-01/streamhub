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
