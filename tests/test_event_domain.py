from __future__ import annotations

import uuid

from streamhub_common.event_identity import build_event_identity, canonical_source_url


def test_same_vod_uses_same_canonical_event_key():
    first = build_event_identity(
        platform="twitch",
        media_type="vod",
        session_id=uuid.uuid4(),
        video_external_id="2885504478",
    )
    second = build_event_identity(
        platform="twitch",
        media_type="vod",
        session_id=uuid.uuid4(),
        video_external_id="2885504478",
    )
    assert first.external_key == second.external_key == "twitch:vod:2885504478"
    assert first.identity_state == "canonical"


def test_same_live_stream_id_uses_same_event_key():
    first = build_event_identity(
        platform="twitch",
        media_type="live",
        session_id=uuid.uuid4(),
        stream_external_id="123456",
    )
    second = build_event_identity(
        platform="twitch",
        media_type="live",
        session_id=uuid.uuid4(),
        stream_external_id="123456",
    )
    assert first.external_key == second.external_key == "twitch:live:123456"


def test_missing_reliable_external_id_never_merges_by_guess():
    session_a = uuid.uuid4()
    session_b = uuid.uuid4()
    first = build_event_identity(platform="twitch", media_type="live", session_id=session_a)
    second = build_event_identity(platform="twitch", media_type="live", session_id=session_b)
    assert first.external_key == f"legacy:{session_a}"
    assert second.external_key == f"legacy:{session_b}"
    assert first.external_key != second.external_key
    assert first.identity_state == "provisional_missing_external_id"


def test_source_url_is_canonical_for_vod_and_live():
    assert canonical_source_url(
        media_type="vod",
        video_external_id="123",
        channel_login=None,
        page_url="https://www.twitch.tv/videos/123?t=1h",
    ) == "https://www.twitch.tv/videos/123"
    assert canonical_source_url(
        media_type="live",
        video_external_id=None,
        channel_login="example",
        page_url="https://www.twitch.tv/example?foo=bar",
    ) == "https://www.twitch.tv/example"
