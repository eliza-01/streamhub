from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..telegram.playback import PlaybackNotFound, PlaybackUnavailable, TelegramPlaybackService
from ..telegram.runtime import telegram_runtime
from ..telegram.storage import TelegramAuthRequired, TelegramStorageError

router = APIRouter(prefix="/api/v1/playback", tags=["playback"])
service = TelegramPlaybackService(telegram_runtime)


def _stream_response(plan) -> StreamingResponse:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(plan.content_length),
        "Cache-Control": "private, max-age=31536000, immutable",
    }
    if plan.content_range:
        headers["Content-Range"] = plan.content_range
    return StreamingResponse(
        plan.iterator,
        status_code=plan.status_code,
        media_type=plan.media_type,
        headers=headers,
    )


@router.get("/video-sessions/{session_id}")
async def playback_status(session_id: uuid.UUID) -> dict:
    try:
        return await service.session_status(session_id)
    except PlaybackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/video-sessions/{session_id}/index.m3u8")
async def playlist(session_id: uuid.UUID):
    try:
        return PlainTextResponse(
            await service.playlist(session_id),
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )
    except PlaybackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlaybackUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/segments/{part_id}/{segment_no}.ts")
async def segment(
    part_id: uuid.UUID,
    segment_no: int,
    session_id: str | None = None,
    range_header: str | None = Header(default=None, alias="Range"),
):
    _ = session_id
    try:
        return _stream_response(await service.segment_stream(part_id, segment_no, range_header))
    except PlaybackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlaybackUnavailable as exc:
        raise HTTPException(status_code=416 if "range" in str(exc).lower() else 409, detail=str(exc)) from exc
    except TelegramAuthRequired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TelegramStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/parts/{part_id}.ts")
async def raw_part(part_id: uuid.UUID, range_header: str | None = Header(default=None, alias="Range")):
    try:
        return _stream_response(await service.part_stream(part_id, range_header))
    except PlaybackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlaybackUnavailable as exc:
        raise HTTPException(status_code=416 if "range" in str(exc).lower() else 409, detail=str(exc)) from exc
    except TelegramAuthRequired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TelegramStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
