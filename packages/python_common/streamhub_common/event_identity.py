from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class EventIdentity:
    external_key: str
    identity_state: str


def build_event_identity(
    *,
    platform: str,
    media_type: str,
    session_id: uuid.UUID,
    video_external_id: str | None = None,
    stream_external_id: str | None = None,
) -> EventIdentity:
    normalized_platform = (platform or "twitch").lower()
    if media_type == "vod" and video_external_id:
        return EventIdentity(f"{normalized_platform}:vod:{video_external_id}", "canonical")
    if media_type == "live" and stream_external_id:
        return EventIdentity(f"{normalized_platform}:live:{stream_external_id}", "canonical")
    return EventIdentity(f"legacy:{session_id}", "provisional_missing_external_id")


def canonical_source_url(
    *,
    media_type: str,
    video_external_id: str | None,
    channel_login: str | None,
    page_url: str | None,
) -> str | None:
    if media_type == "vod" and video_external_id:
        return f"https://www.twitch.tv/videos/{video_external_id}"
    if media_type == "live" and channel_login:
        return f"https://www.twitch.tv/{channel_login}"
    return page_url
