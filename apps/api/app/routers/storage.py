from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import StorageOutputSetting
from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
settings = get_settings()


class OutputSettingsUpdate(BaseModel):
    output_root_key: str
    output_subdir: str
    batch_segments: int = Field(default=100, ge=1, le=1000)


def available_output_roots() -> list[dict[str, str]]:
    roots = [
        {"key": "root1", "label": settings.video_output_root_1_label},
    ]
    if settings.video_output_root_2_enabled:
        roots.append({"key": "root2", "label": settings.video_output_root_2_label})
    if settings.video_output_root_3_enabled:
        roots.append({"key": "root3", "label": settings.video_output_root_3_label})
    return roots


def normalize_output_subdir(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw:
        raise HTTPException(400, "output subdirectory is required")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(400, "output subdirectory must be a safe relative path")
    if any(":" in part for part in path.parts):
        raise HTTPException(400, "drive letters belong to the configured output root, not the subdirectory")
    return path.as_posix()


async def get_or_create_output_setting(db: AsyncSession) -> StorageOutputSetting:
    row = await db.get(StorageOutputSetting, 1)
    if row is None:
        row = StorageOutputSetting(
            id=1,
            output_root_key="root1",
            output_subdir="streamhub",
            batch_segments=settings.video_archive_batch_segments,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def payload(row: StorageOutputSetting) -> dict:
    roots = available_output_roots()
    return {
        "output_root_key": row.output_root_key,
        "output_subdir": row.output_subdir,
        "roots": roots,
        "batch_segments": int(row.batch_segments or settings.video_archive_batch_segments),
        "applies_to": "new_video_sessions",
    }


@router.get("/output-settings")
async def read_output_settings(db: AsyncSession = Depends(get_db)) -> dict:
    return payload(await get_or_create_output_setting(db))


@router.put("/output-settings")
async def update_output_settings(body: OutputSettingsUpdate, db: AsyncSession = Depends(get_db)) -> dict:
    roots = {item["key"] for item in available_output_roots()}
    if body.output_root_key not in roots:
        raise HTTPException(400, "output root is not enabled")
    row = await get_or_create_output_setting(db)
    row.output_root_key = body.output_root_key
    row.output_subdir = normalize_output_subdir(body.output_subdir)
    row.batch_segments = body.batch_segments
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(row)
    return payload(row)
