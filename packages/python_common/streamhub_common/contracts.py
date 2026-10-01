from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceKind = Literal["eventsub", "irc", "vod_replay_api", "vod_browser_fallback"]


class ChatMessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider_message_id: str | None = None
    provider_event_id: str | None = None
    source_kind: SourceKind
    source_created_at_utc: datetime | None = None
    client_observed_at_utc: datetime | None = None
    media_offset_ms: int | None = None
    timeline_offset_ms: int | None = None
    chatter_external_id: str | None = None
    chatter_login: str | None = None
    chatter_name: str | None = None
    color: str | None = None
    badges_json: Any | None = None
    message_text: str = ""
    fragments_json: Any | None = None
    reply_json: dict[str, Any] | None = None
    bits: int | None = None
    is_action: bool = False
    raw_payload_json: Any | None = None
    message_type: str | None = None
    channel_points_reward_id: str | None = None
    source_broadcaster_external_id: str | None = None
    source_broadcaster_login: str | None = None
    source_broadcaster_name: str | None = None


class ChatBatch(BaseModel):
    batch_id: uuid.UUID
    session_id: uuid.UUID
    sent_at: datetime
    schema_version: int = 1
    producer_instance_id: str
    source_kind: SourceKind
    messages: list[ChatMessageEnvelope] = Field(default_factory=list)


class ChatEventEnvelope(BaseModel):
    event_type: str
    timeline_offset_ms: int = 0
    provider_event_id: str | None = None
    source_kind: SourceKind | None = None
    payload_json: Any | None = None


class EventBatch(BaseModel):
    batch_id: uuid.UUID
    session_id: uuid.UUID
    sent_at: datetime
    schema_version: int = 1
    producer_instance_id: str
    source_kind: SourceKind | None = None
    events: list[ChatEventEnvelope] = Field(default_factory=list)
