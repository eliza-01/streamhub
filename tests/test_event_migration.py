from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path


def load_migration_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "migrations/versions/0002_event_domain.py"
    spec = importlib.util.spec_from_file_location("streamhub_0002_event_domain", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_groups_equal_vod_ids():
    migration = load_migration_module()
    first = {
        "id": uuid.uuid4().bytes,
        "platform": "twitch",
        "media_type": "vod",
        "video_external_id": "2885504478",
        "stream_external_id": None,
    }
    second = {**first, "id": uuid.uuid4().bytes}
    assert migration._external_key(first) == migration._external_key(second) == "twitch:vod:2885504478"


def test_backfill_groups_equal_live_stream_ids():
    migration = load_migration_module()
    first = {
        "id": uuid.uuid4().bytes,
        "platform": "twitch",
        "media_type": "live",
        "video_external_id": None,
        "stream_external_id": "777",
    }
    second = {**first, "id": uuid.uuid4().bytes}
    assert migration._external_key(first) == migration._external_key(second) == "twitch:live:777"


def test_backfill_never_guesses_identity_without_external_id():
    migration = load_migration_module()
    first = {
        "id": uuid.uuid4().bytes,
        "platform": "twitch",
        "media_type": "live",
        "video_external_id": None,
        "stream_external_id": None,
    }
    second = {**first, "id": uuid.uuid4().bytes}
    assert migration._external_key(first).startswith("legacy:")
    assert migration._external_key(second).startswith("legacy:")
    assert migration._external_key(first) != migration._external_key(second)
