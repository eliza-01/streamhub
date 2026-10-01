from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1/auth/twitch", tags=["twitch-auth"])
settings = get_settings()


async def adapter_request(method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"X-Internal-Service-Token": settings.internal_service_token}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.request(method, f"{settings.twitch_adapter_base_url}{path}", json=payload, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"twitch-adapter auth error: {response.text[:500]}")
    return response.json()


@router.post("/device/start")
async def device_start() -> dict:
    return await adapter_request("POST", "/internal/v1/auth/twitch/device/start", {})


@router.get("/device/{request_id}")
async def device_status(request_id: str) -> dict:
    return await adapter_request("GET", f"/internal/v1/auth/twitch/device/{request_id}")
