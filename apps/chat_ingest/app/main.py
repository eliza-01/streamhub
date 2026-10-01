from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.contracts import ChatBatch, EventBatch
from streamhub_common.db import get_db
from streamhub_common.logging import configure_logging
from streamhub_common.models import ChatEvent, ChatMessage, Session
from streamhub_common.security import require_internal_token
from streamhub_common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(title="StreamHub Chat Ingest", version="0.1.0")


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


def stable_dedup_key(session_id: str, message: dict) -> bytes:
    if message.get("provider_message_id"):
        raw = f"provider:{session_id}:{message['provider_message_id']}".encode()
    else:
        core = {
            "session_id": session_id,
            "source_kind": message.get("source_kind"),
            "chatter_external_id": message.get("chatter_external_id"),
            "chatter_login": message.get("chatter_login"),
            "message_text": message.get("message_text"),
            "fragments_json": message.get("fragments_json"),
            "media_offset_ms": message.get("media_offset_ms"),
            "source_created_at_utc": str(message.get("source_created_at_utc")),
            "message_type": message.get("message_type"),
        }
        raw = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).digest()


def effective_timestamp(message: dict, server_received: datetime) -> tuple[datetime, str]:
    if message.get("source_created_at_utc"):
        return message["source_created_at_utc"].replace(tzinfo=None), "provider_source"
    if message.get("client_observed_at_utc"):
        return message["client_observed_at_utc"].replace(tzinfo=None), "client_observed"
    return server_received, "server_received"


@app.post("/internal/v1/ingest/messages:batch", dependencies=[Depends(require_internal_token)])
async def ingest_messages(batch: ChatBatch, db: AsyncSession = Depends(get_db)) -> dict:
    if len(batch.messages) > settings.ingest_batch_max:
        raise HTTPException(413, f"batch exceeds INGEST_BATCH_MAX={settings.ingest_batch_max}")

    session = (
        await db.execute(select(Session).where(Session.id == batch.session_id).with_for_update())
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "session not found")

    accepted = 0
    duplicates = 0
    rejected: list[dict] = []
    now = datetime.now(UTC).replace(tzinfo=None)

    for idx, envelope in enumerate(batch.messages):
        data = envelope.model_dump()
        dedup = stable_dedup_key(str(batch.session_id), data)
        duplicate_q = select(ChatMessage.id).where(
            ChatMessage.session_id == batch.session_id,
            ChatMessage.dedup_key == dedup,
        )
        if await db.scalar(duplicate_q):
            duplicates += 1
            continue

        eff, source = effective_timestamp(data, now)
        timeline = data.get("timeline_offset_ms")
        if timeline is None:
            timeline = data.get("media_offset_ms") or 0

        row = ChatMessage(
            session_id=batch.session_id,
            sequence_no=session.next_sequence_no,
            provider_message_id=data.get("provider_message_id"),
            provider_event_id=data.get("provider_event_id"),
            dedup_key=dedup,
            source_kind=data["source_kind"],
            source_created_at_utc=data.get("source_created_at_utc").replace(tzinfo=None) if data.get("source_created_at_utc") else None,
            client_observed_at_utc=data.get("client_observed_at_utc").replace(tzinfo=None) if data.get("client_observed_at_utc") else None,
            server_received_at_utc=now,
            effective_message_time_utc=eff,
            timestamp_source=source,
            media_offset_ms=data.get("media_offset_ms"),
            timeline_offset_ms=timeline,
            chatter_external_id=data.get("chatter_external_id"),
            chatter_login=data.get("chatter_login"),
            chatter_name=data.get("chatter_name"),
            color=data.get("color"),
            badges_json=data.get("badges_json"),
            message_text=data.get("message_text") or "",
            fragments_json=data.get("fragments_json"),
            reply_json=data.get("reply_json"),
            bits=data.get("bits"),
            is_action=bool(data.get("is_action")),
            raw_payload_json=data.get("raw_payload_json"),
            schema_version=batch.schema_version,
            message_type=data.get("message_type"),
            channel_points_reward_id=data.get("channel_points_reward_id"),
            source_broadcaster_external_id=data.get("source_broadcaster_external_id"),
            source_broadcaster_login=data.get("source_broadcaster_login"),
            source_broadcaster_name=data.get("source_broadcaster_name"),
        )
        db.add(row)
        session.next_sequence_no += 1
        accepted += 1

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "idempotency conflict while committing batch") from exc

    return {"accepted_count": accepted, "duplicate_count": duplicates, "rejected": rejected}


@app.post("/internal/v1/ingest/events:batch", dependencies=[Depends(require_internal_token)])
async def ingest_events(batch: EventBatch, db: AsyncSession = Depends(get_db)) -> dict:
    session = (
        await db.execute(select(Session).where(Session.id == batch.session_id).with_for_update())
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "session not found")

    accepted = 0
    for envelope in batch.events:
        data = envelope.model_dump()
        db.add(
            ChatEvent(
                session_id=batch.session_id,
                sequence_no=session.next_sequence_no,
                event_type=data["event_type"],
                timeline_offset_ms=data.get("timeline_offset_ms") or 0,
                provider_event_id=data.get("provider_event_id"),
                source_kind=data.get("source_kind"),
                payload_json=data.get("payload_json"),
            )
        )
        session.next_sequence_no += 1
        accepted += 1

        if data["event_type"] == "message_delete" and isinstance(data.get("payload_json"), dict):
            target = data["payload_json"].get("provider_message_id") or data["payload_json"].get("message_id")
            if target:
                await db.execute(
                    update(ChatMessage)
                    .where(ChatMessage.session_id == batch.session_id, ChatMessage.provider_message_id == target)
                    .values(is_deleted=True, deleted_at_utc=datetime.now(UTC).replace(tzinfo=None))
                )

    await db.commit()
    return {"accepted_count": accepted, "duplicate_count": 0, "rejected": []}
