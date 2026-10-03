from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..telegram.runtime import telegram_runtime
from ..telegram.storage import TelegramAuthRequired, TelegramStorageError

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


@router.get("/status")
async def telegram_status() -> dict:
    return await telegram_runtime.status()


@router.post("/sync")
async def telegram_sync() -> dict:
    try:
        result = await telegram_runtime.sync_once(reconcile_missing=True)
        return {"ok": True, **result}
    except TelegramAuthRequired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TelegramStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/bindings")
async def telegram_bindings(session_id: str | None = Query(default=None)) -> dict:
    try:
        return {"ok": True, "bindings": await telegram_runtime.bindings(session_id=session_id)}
    except TelegramStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
