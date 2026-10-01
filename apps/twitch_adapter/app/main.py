from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from streamhub_common.contracts import ChatBatch, ChatMessageEnvelope
from streamhub_common.db import SessionLocal
from streamhub_common.logging import configure_logging
from streamhub_common.models import AuthToken, CaptureJob, OAuthAccount, Session
from streamhub_common.security import encrypt_secret, require_internal_token
from streamhub_common.settings import get_settings

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


async def send_ingest_batch(session_id: uuid.UUID, messages: list[ChatMessageEnvelope]) -> dict:
    if not messages:
        return {"accepted_count": 0, "duplicate_count": 0, "rejected": []}
    batch = ChatBatch(
        batch_id=uuid.uuid4(),
        session_id=session_id,
        sent_at=datetime.now(UTC),
        schema_version=1,
        producer_instance_id="twitch-adapter",
        source_kind="vod_replay_api",
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


async def poll_device_code(request_id: str, device_code: str, interval: int, expires_in: int) -> None:
    deadline = asyncio.get_running_loop().time() + expires_in
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(max(1, interval))
        data = {
            "client_id": settings.twitch_client_id,
            "scopes": settings.twitch_device_scopes,
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
    yield
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
    raise HTTPException(
        501,
        "LIVE EventSub + IRC redundant collector is intentionally not claimed complete in this initial increment",
    )


@app.post("/internal/v1/live-collectors/{session_id}/pause", dependencies=[Depends(require_internal_token)])
async def pause_live_collectors(session_id: uuid.UUID) -> dict:
    raise HTTPException(501, "LIVE collector not implemented yet")


@app.post("/internal/v1/live-collectors/{session_id}/resume", dependencies=[Depends(require_internal_token)])
async def resume_live_collectors(session_id: uuid.UUID) -> dict:
    raise HTTPException(501, "LIVE collector not implemented yet")


@app.post("/internal/v1/live-collectors/{session_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_live_collectors(session_id: uuid.UUID, payload: StopRequest) -> dict:
    raise HTTPException(501, "LIVE collector not implemented yet")


@app.post("/internal/v1/auth/twitch/device/start", dependencies=[Depends(require_internal_token)])
async def auth_device_start() -> dict:
    data = {"client_id": settings.twitch_client_id, "scopes": settings.twitch_device_scopes}
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
