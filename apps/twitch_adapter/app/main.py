from __future__ import annotations

import asyncio
import json
import logging
import ssl
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from websockets.exceptions import ConnectionClosed

from streamhub_common.contracts import ChatBatch, ChatEventEnvelope, ChatMessageEnvelope, EventBatch, SourceKind
from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import AuthToken, CaptureJob, OAuthAccount, Session
from streamhub_common.security import decrypt_secret, encrypt_secret, require_internal_token
from streamhub_common.settings import get_settings

from app.live import eventsub_chat_delete, eventsub_chat_message, irc_chat_event, irc_chat_message, parse_irc_line

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("streamhub.twitch_adapter")


class TwitchIntegrityContext(BaseModel):
    client_integrity: str
    device_id: str
    client_id: str | None = None
    client_version: str | None = None
    client_session_id: str | None = None
    authorization: str | None = None
    user_agent: str | None = None
    captured_at: datetime | None = None


vod_tasks: dict[uuid.UUID, asyncio.Task] = {}
vod_integrity_contexts: dict[uuid.UUID, TwitchIntegrityContext] = {}
auth_requests: dict[str, dict[str, Any]] = {}


@dataclass(slots=True)
class TwitchRuntimeAuth:
    access_token: str
    user_id: str
    login: str
    scopes: set[str]


@dataclass
class LiveCollectorRuntime:
    session_id: uuid.UUID
    channel_login: str
    broadcaster_id: str
    broadcaster_name: str | None
    auth: TwitchRuntimeAuth
    origin: datetime
    capture_start_offset_ms: int
    irc_enabled: bool
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    eventsub_ready: asyncio.Event = field(default_factory=asyncio.Event)
    message_queue: asyncio.Queue[ChatMessageEnvelope] = field(
        default_factory=lambda: asyncio.Queue(maxsize=min(settings.provider_outbox_max_messages, 50_000))
    )
    event_queue: asyncio.Queue[ChatEventEnvelope] = field(default_factory=lambda: asyncio.Queue(maxsize=10_000))
    source_connected: dict[str, bool] = field(default_factory=lambda: {"eventsub": False, "irc": False})
    source_errors: dict[str, str | None] = field(default_factory=lambda: {"eventsub": None, "irc": None})
    accepted_messages: int = 0
    duplicate_messages: int = 0
    retry_count: int = 0
    last_offset_ms: int = 0
    _seen_eventsub_ids: set[str] = field(default_factory=set)
    _seen_eventsub_order: deque[str] = field(default_factory=lambda: deque(maxlen=5_000))

    def remember_eventsub_id(self, message_id: str | None) -> bool:
        if not message_id:
            return True
        if message_id in self._seen_eventsub_ids:
            return False
        if len(self._seen_eventsub_order) == self._seen_eventsub_order.maxlen:
            oldest = self._seen_eventsub_order.popleft()
            self._seen_eventsub_ids.discard(oldest)
        self._seen_eventsub_order.append(message_id)
        self._seen_eventsub_ids.add(message_id)
        return True


live_runtimes: dict[uuid.UUID, LiveCollectorRuntime] = {}
live_tasks: dict[uuid.UUID, asyncio.Task] = {}


class VodStartRequest(BaseModel):
    session_id: uuid.UUID
    video_id: str
    duration_ms: int | None = None
    twitch_integrity: TwitchIntegrityContext | None = None


class LiveStartRequest(BaseModel):
    session_id: uuid.UUID
    channel_login: str


class StopRequest(BaseModel):
    reason: str = "stop_user"


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parse_twitch_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def persist_token(access_token: str, refresh_token: str | None, expires_in: int | None, scope: list[str] | None) -> dict:
    headers = {"Authorization": f"OAuth {access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        validation = await client.get(settings.twitch_validate_url, headers=headers)
    if validation.status_code != 200:
        raise RuntimeError(f"token validation failed: {validation.text[:300]}")
    profile = validation.json()
    user_id = str(profile.get("user_id") or "")
    if not user_id:
        raise RuntimeError("validated token has no user_id")

    async with SessionLocal() as db:
        account = (
            await db.execute(
                select(OAuthAccount).where(OAuthAccount.provider == "twitch", OAuthAccount.provider_user_id == user_id)
            )
        ).scalar_one_or_none()
        if account is None:
            account = OAuthAccount(provider="twitch", provider_user_id=user_id)
            db.add(account)
            await db.flush()
        account.login = profile.get("login")
        account.scopes_json = profile.get("scopes") or scope or []
        account.validated_at = utcnow_naive()

        existing = (
            await db.execute(
                select(AuthToken)
                .where(AuthToken.oauth_account_id == account.id, AuthToken.replaced_at.is_(None))
                .order_by(AuthToken.id.desc())
            )
        ).scalars().first()
        if existing:
            existing.replaced_at = utcnow_naive()
        expires_at = utcnow_naive() + timedelta(seconds=int(expires_in or profile.get("expires_in") or 0))
        db.add(
            AuthToken(
                oauth_account_id=account.id,
                access_token_encrypted=encrypt_secret(access_token),
                refresh_token_encrypted=encrypt_secret(refresh_token) if refresh_token else None,
                expires_at=expires_at,
            )
        )
        await db.commit()
    return profile


async def validate_legacy_token() -> None:
    token = settings.normalized_legacy_token
    if not token or not settings.twitch_use_legacy_token_if_valid:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(settings.twitch_validate_url, headers={"Authorization": f"OAuth {token}"})
        if response.status_code != 200:
            logger.info("legacy Twitch token is not valid; Device Code Flow will be used")
            return
        data = response.json()
        required = set(settings.twitch_device_scopes.split())
        scopes = set(data.get("scopes") or [])
        if not required.issubset(scopes):
            logger.info("legacy Twitch token lacks required EventSub scopes; Device Code Flow will be used")
            return
        await persist_token(token, None, data.get("expires_in"), list(scopes))
        logger.info("validated legacy Twitch token imported as bootstrap runtime token")
    except Exception:
        logger.exception("legacy Twitch token validation failed without stopping service")


async def send_ingest_batch(
    session_id: uuid.UUID,
    messages: list[ChatMessageEnvelope],
    source_kind: SourceKind | None = None,
) -> dict:
    if not messages:
        return {"accepted_count": 0, "duplicate_count": 0, "rejected": []}
    batch = ChatBatch(
        batch_id=uuid.uuid4(),
        session_id=session_id,
        sent_at=datetime.now(UTC),
        schema_version=1,
        producer_instance_id="twitch-adapter",
        source_kind=source_kind or messages[0].source_kind,
        messages=messages,
    )
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.chat_ingest_base_url}/internal/v1/ingest/messages:batch",
            json=batch.model_dump(mode="json"),
            headers=headers,
        )
    response.raise_for_status()
    return response.json()


async def send_ingest_event_batch(
    session_id: uuid.UUID,
    events: list[ChatEventEnvelope],
    source_kind: SourceKind | None = None,
) -> dict:
    if not events:
        return {"accepted_count": 0, "duplicate_count": 0, "rejected": []}
    batch = EventBatch(
        batch_id=uuid.uuid4(),
        session_id=session_id,
        sent_at=datetime.now(UTC),
        schema_version=1,
        producer_instance_id="twitch-adapter",
        source_kind=source_kind or events[0].source_kind,
        events=events,
    )
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.chat_ingest_base_url}/internal/v1/ingest/events:batch",
            json=batch.model_dump(mode="json"),
            headers=headers,
        )
    response.raise_for_status()
    return response.json()


def normalize_vod_comment(node: dict[str, Any]) -> ChatMessageEnvelope:
    message = node.get("message") or {}
    commenter = node.get("commenter") or {}
    fragments = message.get("fragments") or []
    badges = message.get("userBadges") or message.get("badges") or []
    offset_seconds = node.get("contentOffsetSeconds")
    media_offset_ms = int(float(offset_seconds) * 1000) if offset_seconds is not None else None
    created_at = parse_twitch_datetime(node.get("createdAt"))
    return ChatMessageEnvelope(
        provider_message_id=str(node.get("id")) if node.get("id") is not None else None,
        source_kind="vod_replay_api",
        source_created_at_utc=created_at,
        media_offset_ms=media_offset_ms,
        timeline_offset_ms=media_offset_ms,
        chatter_external_id=str(commenter.get("id")) if commenter.get("id") is not None else None,
        chatter_login=commenter.get("login") or commenter.get("name"),
        chatter_name=commenter.get("displayName") or commenter.get("display_name") or commenter.get("name"),
        color=commenter.get("color"),
        badges_json=badges,
        message_text=message.get("body") or "",
        fragments_json=fragments,
        raw_payload_json=node,
        message_type="message",
    )


async def fetch_vod_page(
    video_id: str,
    cursor: str | None,
    integrity: TwitchIntegrityContext | None = None,
) -> tuple[list[ChatMessageEnvelope], str | None, bool, int | None]:
    variables: dict[str, Any] = {"videoID": video_id}
    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = settings.vod_replay_start_offset_seconds
    body = {
        "operationName": settings.vod_replay_operation_name,
        "variables": variables,
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": settings.vod_replay_persisted_query_sha256,
            }
        },
    }
    headers = {
        "Client-ID": integrity.client_id if integrity and integrity.client_id else settings.vod_replay_client_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
    }
    if integrity:
        headers["Client-Integrity"] = integrity.client_integrity
        headers["X-Device-Id"] = integrity.device_id
        if integrity.client_version:
            headers["Client-Version"] = integrity.client_version
        if integrity.client_session_id:
            headers["Client-Session-Id"] = integrity.client_session_id
        # Twitch Client-Integrity is bound to the identity context that minted
        # it.  When the browser is logged in, replaying the integrity token
        # without the matching browser OAuth header fails on cursor pages with
        # IntegrityCheckFailed even though the HTTP request itself is 200.
        if integrity.authorization:
            headers["Authorization"] = integrity.authorization
        if integrity.user_agent:
            headers["User-Agent"] = integrity.user_agent
    timeout = httpx.Timeout(settings.vod_replay_request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Twitch's web GQL endpoint expects a batch-shaped JSON array even for
        # a single persisted query. Sending the query object directly returns
        # HTTP 400 before the operation is evaluated.
        response = await client.post(settings.vod_replay_gql_url, json=[body], headers=headers)

    if response.is_error:
        # Preserve Twitch's diagnostic response. It normally contains the
        # actual persisted-query/variables error and does not include our
        # request Authorization header.
        excerpt = response.text.replace("\r", " ").replace("\n", " ").strip()[:2000]
        raise RuntimeError(f"Twitch replay GQL HTTP {response.status_code}: {excerpt or '<empty response>'}")

    raw_payload = response.json()
    if isinstance(raw_payload, list):
        if len(raw_payload) != 1 or not isinstance(raw_payload[0], dict):
            raise RuntimeError(f"Unexpected Twitch replay GQL batch response: {raw_payload!r}")
        payload = raw_payload[0]
    elif isinstance(raw_payload, dict):
        # Keep this fallback for compatibility if Twitch ever returns a
        # single-object response to a one-operation request.
        payload = raw_payload
    else:
        raise RuntimeError(f"Unexpected Twitch replay GQL response type: {type(raw_payload).__name__}")

    if payload.get("errors"):
        raise RuntimeError(f"Twitch replay GQL errors: {payload['errors']!r}")
    comments = (((payload.get("data") or {}).get("video") or {}).get("comments"))
    if not isinstance(comments, dict):
        raise RuntimeError("Twitch replay response does not contain data.video.comments")
    edges = comments.get("edges") or []
    messages: list[ChatMessageEnvelope] = []
    last_offset: int | None = None
    next_cursor: str | None = None
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or {}
        if not isinstance(node, dict):
            continue
        normalized = normalize_vod_comment(node)
        messages.append(normalized)
        if normalized.media_offset_ms is not None:
            last_offset = normalized.media_offset_ms
        edge_cursor = edge.get("cursor")
        if edge_cursor:
            next_cursor = str(edge_cursor)
    page_info = comments.get("pageInfo") or {}
    has_next = bool(page_info.get("hasNextPage"))
    if has_next and not next_cursor:
        raise RuntimeError("Twitch replay says hasNextPage=true but returned no cursor")
    return messages, next_cursor, has_next, last_offset


async def mark_vod_failed(session_id: uuid.UUID, error: str) -> None:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        job = (
            await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat"))
        ).scalar_one_or_none()
        if job:
            job.status = "failed"
            job.last_error = error[:4000]
        if session:
            session.status = "failed"
            session.completeness_status = "failed"
        await db.commit()


async def run_vod_job(session_id: uuid.UUID) -> None:
    try:
        retries = 0
        while True:
            async with SessionLocal() as db:
                job = (
                    await db.execute(
                        select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat")
                    )
                ).scalar_one_or_none()
                session = await db.get(Session, session_id)
                if not job or not session:
                    return
                if job.status in {"paused", "stopped", "complete", "failed"}:
                    return
                cursor = job.checkpoint_cursor
                video_id = session.video_external_id
                if not video_id:
                    await mark_vod_failed(session_id, "VOD session has no video_external_id")
                    return

            try:
                messages, next_cursor, has_next, last_offset = await fetch_vod_page(
                    video_id, cursor, vod_integrity_contexts.get(session_id)
                )
                result = await send_ingest_batch(session_id, messages)
                retries = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retries += 1
                async with SessionLocal() as db:
                    job = (
                        await db.execute(
                            select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat")
                        )
                    ).scalar_one_or_none()
                    if job:
                        job.retry_count += 1
                        job.last_error = str(exc)[:4000]
                        await db.commit()
                if retries > settings.vod_replay_retry_max:
                    await mark_vod_failed(session_id, f"retry limit exceeded: {exc}")
                    return
                integrity_error = "IntegrityCheckFailed" in str(exc) or "failed integrity check" in str(exc).lower()
                delay = (
                    min(settings.vod_replay_retry_base_seconds, 2.0)
                    if integrity_error
                    else min(
                        settings.vod_replay_retry_base_seconds * (2 ** max(0, retries - 1)),
                        settings.vod_replay_retry_max_seconds,
                    )
                )
                logger.warning("VOD replay page failed, retrying in %.1fs: %s", delay, exc)
                await asyncio.sleep(delay)
                continue

            async with SessionLocal() as db:
                job = (
                    await db.execute(
                        select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat")
                    )
                ).scalar_one()
                session = await db.get(Session, session_id)
                job.pages_processed += 1
                job.messages_processed += int(result.get("accepted_count", 0)) + int(result.get("duplicate_count", 0))
                job.checkpoint_cursor = next_cursor
                if last_offset is not None:
                    job.last_offset_ms = last_offset
                job.last_error = None
                if session:
                    if job.status == "paused":
                        session.status = "paused"
                    elif job.status == "stopped":
                        session.status = "stopped_incomplete"
                        session.completeness_status = "incomplete"
                    else:
                        session.status = "recording"
                        session.completeness_status = "collecting"
                    if session.coverage_start_ms is None:
                        session.coverage_start_ms = 0
                    if last_offset is not None:
                        session.coverage_end_ms = max(session.coverage_end_ms or 0, last_offset)
                await db.commit()

            if not has_next:
                async with SessionLocal() as db:
                    job = (
                        await db.execute(
                            select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat")
                        )
                    ).scalar_one()
                    session = await db.get(Session, session_id)
                    job.status = "complete"
                    if session:
                        session.status = "completed"
                        session.completeness_status = "complete"
                        session.coverage_start_ms = 0
                        session.coverage_end_ms = session.source_duration_ms if session.source_duration_ms is not None else (job.last_offset_ms or 0)
                        session.duration_recorded_ms = session.source_duration_ms or job.last_offset_ms or 0
                        session.reconciliation_status = "vod_pagination_end_confirmed"
                        session.recording_ended_at_utc = utcnow_naive()
                    await db.commit()
                return
    finally:
        vod_tasks.pop(session_id, None)


def ensure_vod_task(session_id: uuid.UUID) -> None:
    task = vod_tasks.get(session_id)
    if task and not task.done():
        return
    vod_tasks[session_id] = asyncio.create_task(run_vod_job(session_id), name=f"vod-{session_id}")


async def resume_jobs() -> None:
    async with SessionLocal() as db:
        jobs = (
            await db.execute(select(CaptureJob).where(CaptureJob.job_kind == "vod_full_chat", CaptureJob.status == "running"))
        ).scalars().all()
    for job in jobs:
        ensure_vod_task(job.session_id)


def _aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def load_runtime_twitch_auth() -> TwitchRuntimeAuth:
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(AuthToken, OAuthAccount)
                .join(OAuthAccount, OAuthAccount.id == AuthToken.oauth_account_id)
                .where(AuthToken.replaced_at.is_(None), OAuthAccount.provider == "twitch")
                .order_by(AuthToken.id.desc())
            )
        ).first()
    if not row:
        raise RuntimeError("Twitch authorization is missing; authorize Twitch in the extension first")

    token_row, account = row
    try:
        access_token = decrypt_secret(token_row.access_token_encrypted)
    except Exception as exc:
        raise RuntimeError("stored Twitch access token cannot be decrypted") from exc

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            settings.twitch_validate_url,
            headers={"Authorization": f"OAuth {access_token}"},
        )
    if response.status_code != 200:
        raise RuntimeError("Twitch authorization expired or was revoked; authorize Twitch again")
    profile = response.json()
    scopes = set(profile.get("scopes") or account.scopes_json or [])
    if "user:read:chat" not in scopes:
        raise RuntimeError("Twitch authorization lacks user:read:chat; authorize Twitch again")
    user_id = str(profile.get("user_id") or account.provider_user_id or "")
    login = str(profile.get("login") or account.login or "")
    if not user_id or not login:
        raise RuntimeError("validated Twitch authorization has no user identity")
    return TwitchRuntimeAuth(access_token=access_token, user_id=user_id, login=login, scopes=scopes)


def helix_headers(auth: TwitchRuntimeAuth) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {auth.access_token}",
        "Client-Id": settings.twitch_client_id,
        "Accept": "application/json",
    }


async def resolve_broadcaster(auth: TwitchRuntimeAuth, channel_login: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{settings.twitch_api_base_url}/users",
            params={"login": channel_login},
            headers=helix_headers(auth),
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Twitch Get Users failed HTTP {response.status_code}: {response.text[:500]}")
    rows = response.json().get("data") or []
    if not rows:
        raise RuntimeError(f"Twitch channel {channel_login!r} was not found")
    return rows[0]


async def resolve_live_stream(auth: TwitchRuntimeAuth, broadcaster_id: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{settings.twitch_api_base_url}/streams",
            params={"user_id": broadcaster_id},
            headers=helix_headers(auth),
        )
    if response.status_code >= 400:
        logger.warning("Twitch Get Streams failed HTTP %s: %s", response.status_code, response.text[:300])
        return None
    rows = response.json().get("data") or []
    return rows[0] if rows else None


async def create_eventsub_subscription(
    auth: TwitchRuntimeAuth,
    websocket_session_id: str,
    broadcaster_id: str,
    subscription_type: str,
) -> None:
    body = {
        "type": subscription_type,
        "version": "1",
        "condition": {"broadcaster_user_id": broadcaster_id, "user_id": auth.user_id},
        "transport": {"method": "websocket", "session_id": websocket_session_id},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{settings.twitch_api_base_url}/eventsub/subscriptions",
            json=body,
            headers={**helix_headers(auth), "Content-Type": "application/json"},
        )
    if response.status_code != 202:
        raise RuntimeError(
            f"EventSub {subscription_type} subscription failed HTTP {response.status_code}: {response.text[:1000]}"
        )


async def _eventsub_welcome(ws: Any) -> tuple[str, int]:
    raw = await asyncio.wait_for(ws.recv(), timeout=20)
    frame = json.loads(raw)
    if (frame.get("metadata") or {}).get("message_type") != "session_welcome":
        raise RuntimeError("Twitch EventSub WebSocket did not send session_welcome first")
    session = (frame.get("payload") or {}).get("session") or {}
    session_id = str(session.get("id") or "")
    if not session_id:
        raise RuntimeError("Twitch EventSub welcome has no session id")
    keepalive = int(session.get("keepalive_timeout_seconds") or 10)
    return session_id, keepalive


async def run_eventsub_source(runtime: LiveCollectorRuntime) -> None:
    backoff = 1.0
    while not runtime.stop_event.is_set():
        ws = None
        try:
            runtime.auth = await load_runtime_twitch_auth()
            ws = await websockets.connect(
                settings.twitch_eventsub_ws_url,
                open_timeout=20,
                ping_interval=None,
                max_size=4 * 1024 * 1024,
            )
            websocket_session_id, keepalive = await _eventsub_welcome(ws)
            await create_eventsub_subscription(
                runtime.auth, websocket_session_id, runtime.broadcaster_id, "channel.chat.message"
            )
            await create_eventsub_subscription(
                runtime.auth, websocket_session_id, runtime.broadcaster_id, "channel.chat.message_delete"
            )
            runtime.source_connected["eventsub"] = True
            runtime.source_errors["eventsub"] = None
            runtime.eventsub_ready.set()
            backoff = 1.0
            logger.info(
                "LIVE EventSub subscribed session=%s channel=%s websocket_session=%s",
                runtime.session_id,
                runtime.channel_login,
                websocket_session_id,
            )

            while not runtime.stop_event.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=keepalive + 5)
                frame = json.loads(raw)
                metadata = frame.get("metadata") or {}
                message_type = metadata.get("message_type")

                if message_type == "session_keepalive":
                    continue
                if message_type == "session_reconnect":
                    reconnect_url = (((frame.get("payload") or {}).get("session") or {}).get("reconnect_url"))
                    if not reconnect_url:
                        raise RuntimeError("EventSub session_reconnect had no reconnect_url")
                    new_ws = await websockets.connect(
                        reconnect_url,
                        open_timeout=20,
                        ping_interval=None,
                        max_size=4 * 1024 * 1024,
                    )
                    _new_session_id, new_keepalive = await _eventsub_welcome(new_ws)
                    old_ws = ws
                    ws = new_ws
                    keepalive = new_keepalive
                    await old_ws.close()
                    logger.info("LIVE EventSub seamless reconnect session=%s", runtime.session_id)
                    continue
                if message_type == "revocation":
                    subscription = (frame.get("payload") or {}).get("subscription") or {}
                    raise RuntimeError(
                        f"EventSub subscription revoked: {subscription.get('type')} status={subscription.get('status')}"
                    )
                if message_type != "notification":
                    continue
                event_id = str(metadata.get("message_id") or "")
                if not runtime.remember_eventsub_id(event_id):
                    continue
                subscription_type = metadata.get("subscription_type")
                if subscription_type == "channel.chat.message":
                    message = eventsub_chat_message(frame, runtime.origin)
                    if message is not None:
                        await runtime.message_queue.put(message)
                elif subscription_type == "channel.chat.message_delete":
                    event = eventsub_chat_delete(frame, runtime.origin)
                    if event is not None:
                        await runtime.event_queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.source_connected["eventsub"] = False
            runtime.source_errors["eventsub"] = str(exc)[:1000]
            runtime.retry_count += 1
            logger.warning("LIVE EventSub reconnect in %.1fs session=%s: %s", backoff, runtime.session_id, exc)
            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)
        finally:
            runtime.source_connected["eventsub"] = False
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass


async def run_irc_source(runtime: LiveCollectorRuntime) -> None:
    backoff = 1.0
    while not runtime.stop_event.is_set():
        writer: asyncio.StreamWriter | None = None
        try:
            auth = await load_runtime_twitch_auth()
            runtime.auth = auth
            if "chat:read" not in auth.scopes:
                runtime.source_errors["irc"] = "missing chat:read scope; authorize Twitch again for IRC redundancy"
                runtime.source_connected["irc"] = False
                try:
                    await asyncio.wait_for(runtime.stop_event.wait(), timeout=15)
                except TimeoutError:
                    pass
                continue

            ssl_context = ssl.create_default_context() if settings.twitch_irc_use_tls else None
            reader, writer = await asyncio.open_connection(
                settings.twitch_irc_host,
                settings.twitch_irc_port,
                ssl=ssl_context,
                server_hostname=settings.twitch_irc_host if ssl_context else None,
            )
            commands = [
                f"PASS oauth:{auth.access_token}\r\n",
                f"NICK {auth.login}\r\n",
                "CAP REQ :twitch.tv/tags twitch.tv/commands\r\n",
                f"JOIN #{runtime.channel_login}\r\n",
            ]
            writer.write("".join(commands).encode("utf-8"))
            await writer.drain()
            runtime.source_connected["irc"] = True
            runtime.source_errors["irc"] = None
            backoff = 1.0
            logger.info("LIVE IRC joined session=%s channel=%s", runtime.session_id, runtime.channel_login)

            while not runtime.stop_event.is_set():
                raw = await asyncio.wait_for(reader.readline(), timeout=360)
                if not raw:
                    raise ConnectionError("Twitch IRC connection closed")
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                parsed = parse_irc_line(line)
                command = parsed.get("command")
                if command == "PING":
                    payload = parsed.get("trailing") or (parsed.get("params") or ["tmi.twitch.tv"])[0]
                    writer.write(f"PONG :{payload}\r\n".encode("utf-8"))
                    await writer.drain()
                    continue
                if command == "RECONNECT":
                    raise ConnectionError("Twitch IRC requested reconnect")
                if command == "NOTICE":
                    notice = str(parsed.get("trailing") or "")
                    if "authentication failed" in notice.lower() or "improperly formatted auth" in notice.lower():
                        raise RuntimeError(f"Twitch IRC authentication failed: {notice}")
                message = irc_chat_message(
                    line,
                    runtime.origin,
                    runtime.broadcaster_id,
                    runtime.channel_login,
                    runtime.broadcaster_name,
                )
                if message is not None:
                    await runtime.message_queue.put(message)
                    continue
                event = irc_chat_event(line, runtime.origin)
                if event is not None:
                    await runtime.event_queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.source_connected["irc"] = False
            runtime.source_errors["irc"] = str(exc)[:1000]
            runtime.retry_count += 1
            logger.warning("LIVE IRC reconnect in %.1fs session=%s: %s", backoff, runtime.session_id, exc)
            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)
        finally:
            runtime.source_connected["irc"] = False
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass


async def run_live_message_ingest(runtime: LiveCollectorRuntime) -> None:
    while not runtime.stop_event.is_set() or not runtime.message_queue.empty():
        try:
            first = await asyncio.wait_for(runtime.message_queue.get(), timeout=0.25)
        except TimeoutError:
            continue
        batch = [first]
        while len(batch) < settings.ingest_batch_max:
            try:
                batch.append(runtime.message_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        groups: dict[SourceKind, list[ChatMessageEnvelope]] = {}
        for message in batch:
            groups.setdefault(message.source_kind, []).append(message)
        try:
            for source_kind, messages in groups.items():
                retry = 0
                while True:
                    try:
                        result = await send_ingest_batch(runtime.session_id, messages, source_kind)
                        runtime.accepted_messages += int(result.get("accepted_count", 0))
                        runtime.duplicate_messages += int(result.get("duplicate_count", 0))
                        for item in messages:
                            runtime.last_offset_ms = max(runtime.last_offset_ms, int(item.timeline_offset_ms or 0))
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        retry += 1
                        runtime.retry_count += 1
                        runtime.source_errors["ingest"] = str(exc)[:1000]
                        await asyncio.sleep(min(0.5 * (2 ** min(retry, 4)), 5.0))
        finally:
            for _ in batch:
                runtime.message_queue.task_done()


async def run_live_event_ingest(runtime: LiveCollectorRuntime) -> None:
    while not runtime.stop_event.is_set() or not runtime.event_queue.empty():
        try:
            first = await asyncio.wait_for(runtime.event_queue.get(), timeout=0.5)
        except TimeoutError:
            continue
        batch = [first]
        while len(batch) < settings.ingest_batch_max:
            try:
                batch.append(runtime.event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        groups: dict[SourceKind | None, list[ChatEventEnvelope]] = {}
        for event in batch:
            groups.setdefault(event.source_kind, []).append(event)
        try:
            for source_kind, events in groups.items():
                retry = 0
                while True:
                    try:
                        await send_ingest_event_batch(runtime.session_id, events, source_kind)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        retry += 1
                        runtime.retry_count += 1
                        runtime.source_errors["ingest_events"] = str(exc)[:1000]
                        await asyncio.sleep(min(0.5 * (2 ** min(retry, 4)), 5.0))
        finally:
            for _ in batch:
                runtime.event_queue.task_done()


async def flush_live_status(runtime: LiveCollectorRuntime) -> None:
    accepted_delta = runtime.accepted_messages
    duplicate_delta = runtime.duplicate_messages
    retry_delta = runtime.retry_count
    last_offset = runtime.last_offset_ms
    runtime.accepted_messages -= accepted_delta
    runtime.duplicate_messages -= duplicate_delta
    runtime.retry_count -= retry_delta

    async with SessionLocal() as db:
        job = (
            await db.execute(
                select(CaptureJob).where(
                    CaptureJob.session_id == runtime.session_id,
                    CaptureJob.job_kind == "live_chat",
                )
            )
        ).scalar_one_or_none()
        session = await db.get(Session, runtime.session_id)
        if job:
            job.messages_processed += accepted_delta
            job.retry_count += retry_delta
            job.last_offset_ms = max(job.last_offset_ms or 0, last_offset)
            errors = {key: value for key, value in runtime.source_errors.items() if value}
            job.last_error = "; ".join(f"{key}: {value}" for key, value in errors.items())[:4000] or None
            meta = dict(job.metadata_json or {})
            meta.update(
                {
                    "eventsub_connected": runtime.source_connected.get("eventsub", False),
                    "irc_connected": runtime.source_connected.get("irc", False),
                    "irc_redundancy_enabled": runtime.irc_enabled,
                    "irc_scope_present": "chat:read" in runtime.auth.scopes,
                    "duplicates_seen": int(meta.get("duplicates_seen") or 0) + duplicate_delta,
                    "message_queue_depth": runtime.message_queue.qsize(),
                    "event_queue_depth": runtime.event_queue.qsize(),
                }
            )
            job.metadata_json = meta
        if session:
            if session.coverage_start_ms is None:
                session.coverage_start_ms = runtime.capture_start_offset_ms
            else:
                session.coverage_start_ms = min(session.coverage_start_ms, runtime.capture_start_offset_ms)
            session.coverage_end_ms = max(session.coverage_end_ms or runtime.capture_start_offset_ms, last_offset)
            session.duration_recorded_ms = max(
                session.duration_recorded_ms or 0,
                max(0, (session.coverage_end_ms or 0) - (session.coverage_start_ms or 0)),
            )
            if session.status not in {"paused", "stopped_incomplete", "failed", "soft_deleted", "completed"}:
                session.status = "recording"
            if session.completeness_status not in {"complete", "failed", "incomplete"}:
                session.completeness_status = "collecting"
            if runtime.irc_enabled and "chat:read" not in runtime.auth.scopes:
                session.reconciliation_status = "live_eventsub_active_irc_scope_missing"
            elif runtime.irc_enabled:
                session.reconciliation_status = "live_eventsub_irc_redundant"
            else:
                session.reconciliation_status = "live_eventsub_only"
        await db.commit()


async def run_live_status_flusher(runtime: LiveCollectorRuntime) -> None:
    try:
        while not runtime.stop_event.is_set():
            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=5)
            except TimeoutError:
                pass
            await flush_live_status(runtime)
    finally:
        await flush_live_status(runtime)


async def run_live_collectors(runtime: LiveCollectorRuntime) -> None:
    source_tasks = [asyncio.create_task(run_eventsub_source(runtime), name=f"eventsub-{runtime.session_id}")]
    if runtime.irc_enabled:
        source_tasks.append(asyncio.create_task(run_irc_source(runtime), name=f"irc-{runtime.session_id}"))
    worker_tasks = [
        asyncio.create_task(run_live_message_ingest(runtime), name=f"live-ingest-{runtime.session_id}"),
        asyncio.create_task(run_live_event_ingest(runtime), name=f"live-events-{runtime.session_id}"),
        asyncio.create_task(run_live_status_flusher(runtime), name=f"live-status-{runtime.session_id}"),
    ]
    try:
        await runtime.stop_event.wait()
    finally:
        for task in source_tasks:
            task.cancel()
        await asyncio.gather(*source_tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(runtime.message_queue.join(), timeout=10)
            await asyncio.wait_for(runtime.event_queue.join(), timeout=10)
        except TimeoutError:
            logger.warning("LIVE collector flush timeout session=%s; marking incomplete", runtime.session_id)
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        try:
            await flush_live_status(runtime)
        except Exception:
            logger.exception("failed final LIVE status flush session=%s", runtime.session_id)
        live_runtimes.pop(runtime.session_id, None)
        live_tasks.pop(runtime.session_id, None)


async def prepare_live_runtime(session_id: uuid.UUID, channel_login: str) -> LiveCollectorRuntime:
    auth = await load_runtime_twitch_auth()
    broadcaster = await resolve_broadcaster(auth, channel_login.lower())
    broadcaster_id = str(broadcaster.get("id") or "")
    broadcaster_login = str(broadcaster.get("login") or channel_login).lower()
    broadcaster_name = broadcaster.get("display_name") or broadcaster_login
    stream = await resolve_live_stream(auth, broadcaster_id)

    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if not session:
            raise RuntimeError("LIVE session not found")
        if session.media_type != "live":
            raise RuntimeError("session is not LIVE")
        session.channel_external_id = broadcaster_id
        session.channel_login = broadcaster_login
        session.channel_display_name = broadcaster_name
        if stream:
            session.stream_external_id = str(stream.get("id") or "") or session.stream_external_id
            session.title = stream.get("title") or session.title
            session.category_id = str(stream.get("game_id") or "") or session.category_id
            session.category_name = stream.get("game_name") or session.category_name
            parsed_started = parse_twitch_datetime(stream.get("started_at"))
            if parsed_started:
                session.source_started_at_utc = parsed_started.replace(tzinfo=None)
        session.status = "recording"
        session.completeness_status = "collecting"
        job = (
            await db.execute(
                select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "live_chat")
            )
        ).scalar_one_or_none()
        if job is None:
            job = CaptureJob(session_id=session_id, job_kind="live_chat", status="running")
            db.add(job)
        else:
            job.status = "running"
            job.last_error = None
        await db.commit()
        recording_start = _aware_utc(session.recording_started_at_utc)
        origin = _aware_utc(session.source_started_at_utc or session.recording_started_at_utc)
        capture_start_offset_ms = max(0, int((recording_start - origin).total_seconds() * 1000))

    runtime = LiveCollectorRuntime(
        session_id=session_id,
        channel_login=broadcaster_login,
        broadcaster_id=broadcaster_id,
        broadcaster_name=broadcaster_name,
        auth=auth,
        origin=origin,
        capture_start_offset_ms=capture_start_offset_ms,
        irc_enabled=settings.live_irc_redundancy_enabled,
        last_offset_ms=capture_start_offset_ms,
    )
    live_runtimes[session_id] = runtime
    live_tasks[session_id] = asyncio.create_task(run_live_collectors(runtime), name=f"live-{session_id}")
    return runtime


async def stop_live_runtime(session_id: uuid.UUID) -> None:
    runtime = live_runtimes.get(session_id)
    task = live_tasks.get(session_id)
    if runtime:
        runtime.stop_event.set()
    if task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=12)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def resume_live_jobs() -> None:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(CaptureJob, Session)
                .join(Session, Session.id == CaptureJob.session_id)
                .where(
                    CaptureJob.job_kind == "live_chat",
                    CaptureJob.status == "running",
                    Session.media_type == "live",
                    Session.deleted_at_utc.is_(None),
                )
            )
        ).all()
    for _job, session in rows:
        if not session.channel_login or session.id in live_tasks:
            continue
        try:
            await prepare_live_runtime(session.id, session.channel_login)
            logger.info("resumed LIVE collectors session=%s", session.id)
        except Exception as exc:
            logger.warning("could not resume LIVE collectors session=%s: %s", session.id, exc)
            async with SessionLocal() as db:
                job = (
                    await db.execute(
                        select(CaptureJob).where(
                            CaptureJob.session_id == session.id,
                            CaptureJob.job_kind == "live_chat",
                        )
                    )
                ).scalar_one_or_none()
                if job:
                    job.last_error = str(exc)[:4000]
                    job.retry_count += 1
                    await db.commit()


async def poll_device_code(request_id: str, device_code: str, interval: int, expires_in: int) -> None:
    deadline = asyncio.get_running_loop().time() + expires_in
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(max(1, interval))
        data = {
            "client_id": settings.twitch_client_id,
            "scopes": settings.twitch_requested_scopes,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(settings.twitch_token_url, data=data)
            if response.status_code == 200:
                token_data = response.json()
                profile = await persist_token(
                    token_data["access_token"],
                    token_data.get("refresh_token"),
                    token_data.get("expires_in"),
                    token_data.get("scope"),
                )
                auth_requests[request_id].update({"status": "authorized", "identity": profile})
                return
            try:
                error = response.json()
            except ValueError:
                error = {"message": response.text}
            message = str(error.get("message") or error.get("error") or "")
            if "authorization_pending" in message:
                continue
            if "slow_down" in message:
                interval += 5
                continue
            auth_requests[request_id].update({"status": "failed", "error": message or f"HTTP {response.status_code}"})
            return
        except Exception as exc:
            auth_requests[request_id].update({"status": "polling", "last_error": str(exc)[:500]})
    auth_requests[request_id].update({"status": "expired"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await validate_legacy_token()
    await resume_jobs()
    await resume_live_jobs()
    yield
    for runtime in list(live_runtimes.values()):
        runtime.stop_event.set()
    if live_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*list(live_tasks.values()), return_exceptions=True),
                timeout=12,
            )
        except TimeoutError:
            for task in list(live_tasks.values()):
                task.cancel()
            await asyncio.gather(*list(live_tasks.values()), return_exceptions=True)
    for task in list(vod_tasks.values()):
        task.cancel()
    if vod_tasks:
        await asyncio.gather(*vod_tasks.values(), return_exceptions=True)


app = FastAPI(title="StreamHub Twitch Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/internal/v1/vod-fetches", dependencies=[Depends(require_internal_token)])
async def start_vod_fetch(payload: VodStartRequest) -> dict:
    if not settings.vod_full_chat_enabled:
        raise HTTPException(503, "VOD full chat capture is disabled")
    async with SessionLocal() as db:
        session = await db.get(Session, payload.session_id)
        if not session:
            raise HTTPException(404, "session not found")
        if session.media_type != "vod":
            raise HTTPException(409, "session is not VOD")
        session.video_external_id = payload.video_id
        if payload.twitch_integrity:
            vod_integrity_contexts[payload.session_id] = payload.twitch_integrity
        if payload.duration_ms is not None:
            session.source_duration_ms = payload.duration_ms
        session.status = "recording"
        session.completeness_status = "collecting"
        job = (
            await db.execute(
                select(CaptureJob).where(CaptureJob.session_id == payload.session_id, CaptureJob.job_kind == "vod_full_chat")
            )
        ).scalar_one_or_none()
        if job is None:
            job = CaptureJob(session_id=payload.session_id, job_kind="vod_full_chat", status="running")
            db.add(job)
        else:
            job.status = "running"
        await db.commit()
    ensure_vod_task(payload.session_id)
    return {"accepted": True, "session_id": str(payload.session_id), "job": "vod_full_chat"}


@app.post("/internal/v1/vod-fetches/{session_id}/integrity", dependencies=[Depends(require_internal_token)])
async def update_vod_integrity(session_id: uuid.UUID, payload: TwitchIntegrityContext) -> dict:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if not session:
            raise HTTPException(404, "session not found")
        if session.media_type != "vod":
            raise HTTPException(409, "Twitch integrity context is only used by VOD capture")
    vod_integrity_contexts[session_id] = payload
    logger.info(
        "updated Twitch integrity context for VOD session %s captured_at=%s browser_auth=%s",
        session_id,
        payload.captured_at,
        "present" if payload.authorization else "absent",
    )
    return {"ok": True, "captured_at": payload.captured_at}


@app.post("/internal/v1/vod-fetches/{session_id}/pause", dependencies=[Depends(require_internal_token)])
async def pause_vod_fetch(session_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        job = (
            await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat"))
        ).scalar_one_or_none()
        if not job:
            raise HTTPException(404, "VOD job not found")
        job.status = "paused"
        await db.commit()
    return {"ok": True, "checkpoint_durable": True}


@app.post("/internal/v1/vod-fetches/{session_id}/resume", dependencies=[Depends(require_internal_token)])
async def resume_vod_fetch(session_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        job = (
            await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat"))
        ).scalar_one_or_none()
        if not job:
            raise HTTPException(404, "VOD job not found")
        if job.status == "complete":
            return {"ok": True, "already_complete": True}
        job.status = "running"
        await db.commit()
    ensure_vod_task(session_id)
    return {"ok": True}


@app.post("/internal/v1/vod-fetches/{session_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_vod_fetch(session_id: uuid.UUID, payload: StopRequest) -> dict:
    async with SessionLocal() as db:
        job = (
            await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat"))
        ).scalar_one_or_none()
        if job:
            job.status = "stopped"
            job.last_error = f"manual stop: {payload.reason}"
        session = await db.get(Session, session_id)
        if session and session.completeness_status != "complete":
            session.status = "stopped_incomplete"
            session.completeness_status = "incomplete"
        await db.commit()
    return {"ok": True}


@app.post("/internal/v1/reconcile/{session_id}", dependencies=[Depends(require_internal_token)])
async def reconcile(session_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if not session:
            raise HTTPException(404, "session not found")
        if session.media_type != "vod":
            raise HTTPException(501, "post-live VOD reconciliation is not implemented in this increment")
        job = (
            await db.execute(select(CaptureJob).where(CaptureJob.session_id == session_id, CaptureJob.job_kind == "vod_full_chat"))
        ).scalar_one_or_none()
        if job is None:
            job = CaptureJob(session_id=session_id, job_kind="vod_full_chat", status="running")
            db.add(job)
        else:
            job.status = "running"
            job.last_error = None
        session.status = "reconciling"
        session.completeness_status = "verifying"
        await db.commit()
    ensure_vod_task(session_id)
    return {"ok": True, "reconciliation_started": True}


@app.post("/internal/v1/live-collectors", dependencies=[Depends(require_internal_token)])
async def start_live_collectors(payload: LiveStartRequest) -> dict:
    if not settings.live_eventsub_enabled:
        raise HTTPException(503, "LIVE EventSub capture is disabled")
    existing = live_runtimes.get(payload.session_id)
    if existing and not existing.stop_event.is_set():
        return {
            "accepted": True,
            "session_id": str(payload.session_id),
            "eventsub": existing.eventsub_ready.is_set(),
            "irc_redundancy": existing.source_connected.get("irc", False),
            "already_running": True,
        }
    runtime: LiveCollectorRuntime | None = None
    try:
        runtime = await prepare_live_runtime(payload.session_id, payload.channel_login)
        await asyncio.wait_for(runtime.eventsub_ready.wait(), timeout=12)
    except Exception as exc:
        detail = (runtime.source_errors.get("eventsub") if runtime else None) or str(exc) or type(exc).__name__
        await stop_live_runtime(payload.session_id)
        async with SessionLocal() as db:
            job = (
                await db.execute(
                    select(CaptureJob).where(
                        CaptureJob.session_id == payload.session_id,
                        CaptureJob.job_kind == "live_chat",
                    )
                )
            ).scalar_one_or_none()
            session = await db.get(Session, payload.session_id)
            if job:
                job.status = "failed"
                job.last_error = detail[:4000]
            if session:
                session.status = "failed"
                session.completeness_status = "failed"
            await db.commit()
        raise HTTPException(502, f"LIVE EventSub startup failed: {detail}") from exc
    return {
        "accepted": True,
        "session_id": str(payload.session_id),
        "eventsub": True,
        "irc_redundancy": runtime.irc_enabled and "chat:read" in runtime.auth.scopes,
        "irc_authorization_required": runtime.irc_enabled and "chat:read" not in runtime.auth.scopes,
    }


@app.post("/internal/v1/live-collectors/{session_id}/pause", dependencies=[Depends(require_internal_token)])
async def pause_live_collectors(session_id: uuid.UUID) -> dict:
    runtime = live_runtimes.get(session_id)
    if not runtime:
        raise HTTPException(404, "LIVE collector not found")
    # LIVE pause is control-plane only. Collectors intentionally keep running
    # so the archive does not acquire a gap while the UI is paused.
    return {"ok": True, "capture_continues": True}


@app.post("/internal/v1/live-collectors/{session_id}/resume", dependencies=[Depends(require_internal_token)])
async def resume_live_collectors(session_id: uuid.UUID) -> dict:
    runtime = live_runtimes.get(session_id)
    if runtime and not runtime.stop_event.is_set():
        return {"ok": True, "already_running": True}
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if not session or session.media_type != "live" or not session.channel_login:
            raise HTTPException(404, "LIVE session not found")
        channel_login = session.channel_login
    runtime = None
    try:
        runtime = await prepare_live_runtime(session_id, channel_login)
        await asyncio.wait_for(runtime.eventsub_ready.wait(), timeout=12)
    except Exception as exc:
        detail = (runtime.source_errors.get("eventsub") if runtime else None) or str(exc) or type(exc).__name__
        await stop_live_runtime(session_id)
        raise HTTPException(502, f"LIVE EventSub resume failed: {detail}") from exc
    return {"ok": True, "eventsub": True, "capture_continues": True}


@app.post("/internal/v1/live-collectors/{session_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_live_collectors(session_id: uuid.UUID, payload: StopRequest) -> dict:
    await stop_live_runtime(session_id)
    async with SessionLocal() as db:
        job = (
            await db.execute(
                select(CaptureJob).where(
                    CaptureJob.session_id == session_id,
                    CaptureJob.job_kind == "live_chat",
                )
            )
        ).scalar_one_or_none()
        session = await db.get(Session, session_id)
        if job:
            job.status = "stopped"
            job.last_error = f"manual stop: {payload.reason}"
        if session and session.completeness_status != "complete":
            session.status = "stopped_incomplete"
            session.completeness_status = "incomplete"
            session.recording_ended_at_utc = utcnow_naive()
            if settings.live_post_stream_vod_reconciliation:
                session.reconciliation_status = "live_stopped_pending_vod_reconciliation"
        await db.commit()
    return {"ok": True, "capture_stopped": True, "complete": False}


@app.post("/internal/v1/auth/twitch/device/start", dependencies=[Depends(require_internal_token)])
async def auth_device_start() -> dict:
    data = {"client_id": settings.twitch_client_id, "scopes": settings.twitch_requested_scopes}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(settings.twitch_device_url, data=data)
    if response.status_code >= 400:
        raise HTTPException(502, f"Twitch device endpoint error: {response.text[:500]}")
    result = response.json()
    request_id = str(uuid.uuid4())
    auth_requests[request_id] = {
        "status": "pending",
        "verification_uri": result.get("verification_uri"),
        "user_code": result.get("user_code"),
        "expires_in": result.get("expires_in"),
        "interval": result.get("interval"),
    }
    asyncio.create_task(
        poll_device_code(
            request_id,
            result["device_code"],
            int(result.get("interval") or 5),
            int(result.get("expires_in") or 1800),
        )
    )
    return {"auth_request_id": request_id, **auth_requests[request_id]}


@app.get("/internal/v1/auth/twitch/device/{request_id}", dependencies=[Depends(require_internal_token)])
async def auth_device_status(request_id: str) -> dict:
    result = auth_requests.get(request_id)
    if not result:
        raise HTTPException(404, "auth request not found")
    safe = dict(result)
    safe.pop("device_code", None)
    return {"auth_request_id": request_id, **safe}
