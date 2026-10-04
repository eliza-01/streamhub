from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from streamhub_common.settings import Settings

SITE_ASSET_SLOTS = ("cover", "frame_1", "frame_2", "frame_3", "frame_4")
ALLOWED_SOURCE_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass(frozen=True)
class PreparedSiteAsset:
    storage_key: str
    sha256: str
    size_bytes: int
    width: int
    height: int
    content_type: str
    original_filename: str | None


def validate_site_asset_slot(slot: str) -> str:
    if slot not in SITE_ASSET_SLOTS:
        raise HTTPException(404, "unknown site asset slot")
    return slot


def site_asset_path(settings: Settings, storage_key: str) -> Path:
    root = Path(settings.site_asset_root).resolve()
    path = (root / storage_key).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(500, "invalid site asset storage key")
    return path


async def prepare_and_store_site_asset(
    upload: UploadFile,
    *,
    event_id: UUID,
    slot: str,
    settings: Settings,
) -> PreparedSiteAsset:
    validate_site_asset_slot(slot)
    raw = await upload.read(settings.site_asset_max_bytes + 1)
    if not raw:
        raise HTTPException(400, "empty image")
    if len(raw) > settings.site_asset_max_bytes:
        raise HTTPException(413, f"image exceeds {settings.site_asset_max_bytes} bytes")

    try:
        source = Image.open(BytesIO(raw))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "file is not a supported image") from exc

    if (source.format or "").upper() not in ALLOWED_SOURCE_FORMATS:
        raise HTTPException(400, "only JPEG, PNG and WebP images are supported")
    if getattr(source, "n_frames", 1) != 1:
        raise HTTPException(400, "animated images are not supported")
    if (
        source.width <= 0
        or source.height <= 0
        or source.width * source.height > settings.site_asset_max_pixels
    ):
        raise HTTPException(400, "image dimensions are outside the allowed range")
    try:
        source.load()
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "image data is corrupted") from exc

    image = ImageOps.exif_transpose(source)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    encoded = BytesIO()
    image.save(encoded, format="WEBP", quality=settings.site_asset_webp_quality, method=6)
    data = encoded.getvalue()
    if len(data) > settings.site_asset_max_bytes:
        raise HTTPException(413, "normalized image is too large")
    digest = hashlib.sha256(data).hexdigest()
    storage_key = f"events/{event_id}/{slot}-{digest[:20]}.webp"
    destination = site_asset_path(settings, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return PreparedSiteAsset(
        storage_key=storage_key,
        sha256=digest,
        size_bytes=len(data),
        width=image.width,
        height=image.height,
        content_type="image/webp",
        original_filename=Path((upload.filename or "").strip()).name[:255] or None,
    )


def remove_site_asset_file(settings: Settings, storage_key: str | None) -> None:
    if not storage_key:
        return
    try:
        site_asset_path(settings, storage_key).unlink(missing_ok=True)
    except OSError:
        # Database state is authoritative. An orphan is safer than failing a
        # successful metadata replacement because cleanup could not run.
        pass
