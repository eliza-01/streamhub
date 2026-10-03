from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from streamhub_common.db import SessionLocal
from streamhub_common.models import (
    TelegramChannelFile,
    TelegramChannelState,
    TelegramVideoPartBinding,
    VideoPart,
)
from streamhub_common.telegram_names import canonical_part_identity

from .storage import TelegramScanResult


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds") + ("Z" if value.tzinfo is None else "")


class TelegramRegistry:
    async def last_message_id(self, channel_id: int) -> int:
        async with SessionLocal() as db:
            state = await db.get(TelegramChannelState, channel_id)
            if state is not None:
                return int(state.last_scanned_message_id or 0)
            value = await db.scalar(
                select(func.coalesce(func.max(TelegramChannelFile.message_id), 0)).where(
                    TelegramChannelFile.channel_id == channel_id
                )
            )
            return int(value or 0)

    async def store_scan(self, scan: TelegramScanResult) -> None:
        async with SessionLocal() as db:
            if scan.files:
                table = TelegramChannelFile.__table__
                values = [
                    {
                        "channel_id": item.channel_id,
                        "message_id": item.message_id,
                        "file_name": item.file_name,
                        "bytes": item.bytes,
                        "mime_type": item.mime_type,
                        "document_id": item.document_id,
                        "message_date_utc": item.message_date_utc,
                    }
                    for item in scan.files
                ]
                for start in range(0, len(values), 500):
                    stmt = mysql_insert(table).values(values[start : start + 500])
                    await db.execute(
                        stmt.on_duplicate_key_update(
                            file_name=stmt.inserted.file_name,
                            bytes=stmt.inserted.bytes,
                            mime_type=stmt.inserted.mime_type,
                            document_id=stmt.inserted.document_id,
                            message_date_utc=stmt.inserted.message_date_utc,
                            updated_at=func.current_timestamp(),
                        )
                    )

            state = await db.get(TelegramChannelState, scan.channel_id)
            now = datetime.utcnow()
            if state is None:
                state = TelegramChannelState(
                    channel_id=scan.channel_id,
                    channel_title=scan.channel_title,
                    account_id=scan.account_id,
                    account_display=scan.account_display,
                    last_scanned_message_id=scan.highest_message_id,
                    last_scan_at_utc=now,
                    last_error=None,
                )
                db.add(state)
            else:
                state.channel_title = scan.channel_title
                state.account_id = scan.account_id
                state.account_display = scan.account_display
                state.last_scanned_message_id = max(
                    int(state.last_scanned_message_id or 0), int(scan.highest_message_id or 0)
                )
                state.last_scan_at_utc = now
                state.last_error = None
            await db.commit()

    async def record_error(self, channel_id: int, error: str) -> None:
        async with SessionLocal() as db:
            state = await db.get(TelegramChannelState, channel_id)
            if state is None:
                state = TelegramChannelState(channel_id=channel_id, last_error=error[:8000])
                db.add(state)
            else:
                state.last_error = error[:8000]
            await db.commit()

    async def unresolved_ready_file_names(self) -> list[str]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(VideoPart.file_name)
                    .outerjoin(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                    .where(VideoPart.status == "ready", TelegramVideoPartBinding.part_id.is_(None))
                    .distinct()
                    .order_by(VideoPart.file_name)
                )
            ).scalars().all()
            return [str(name) for name in rows if name]

    async def match_ready_parts(self, channel_id: int) -> dict[str, int]:
        async with SessionLocal() as db:
            parts = (
                await db.execute(
                    select(VideoPart)
                    .outerjoin(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                    .where(VideoPart.status == "ready", TelegramVideoPartBinding.part_id.is_(None))
                )
            ).scalars().all()
            if not parts:
                return {"bound": 0, "missing": 0, "conflict": 0, "size_mismatch": 0}

            sizes = sorted({int(part.final_bytes or part.expected_bytes or 0) for part in parts if int(part.final_bytes or part.expected_bytes or 0) > 0})
            names = sorted({str(part.file_name) for part in parts})
            candidate_rows: list[TelegramChannelFile] = []
            if sizes:
                candidate_rows.extend(
                    (
                        await db.execute(
                            select(TelegramChannelFile).where(
                                TelegramChannelFile.channel_id == channel_id,
                                TelegramChannelFile.bytes.in_(sizes),
                            )
                        )
                    ).scalars().all()
                )
            if names:
                candidate_rows.extend(
                    (
                        await db.execute(
                            select(TelegramChannelFile).where(
                                TelegramChannelFile.channel_id == channel_id,
                                TelegramChannelFile.file_name.in_(names),
                            )
                        )
                    ).scalars().all()
                )

            unique: dict[tuple[int, int], TelegramChannelFile] = {
                (int(row.channel_id), int(row.message_id)): row for row in candidate_rows
            }
            files = list(unique.values())
            used = {
                (int(channel), int(message))
                for channel, message in (
                    await db.execute(
                        select(
                            TelegramVideoPartBinding.channel_id,
                            TelegramVideoPartBinding.message_id,
                        ).where(TelegramVideoPartBinding.channel_id == channel_id)
                    )
                ).all()
            }

            by_name: dict[str, list[TelegramChannelFile]] = defaultdict(list)
            by_identity: dict[tuple[str, int, int, int], list[TelegramChannelFile]] = defaultdict(list)
            for row in files:
                by_name[str(row.file_name)].append(row)
                identity = canonical_part_identity(row.file_name)
                if identity is not None:
                    by_identity[identity].append(row)

            stats = {"bound": 0, "missing": 0, "conflict": 0, "size_mismatch": 0}
            for part in parts:
                name = str(part.file_name)
                size = int(part.final_bytes or part.expected_bytes or 0)
                exact_name = by_name.get(name, [])
                exact_size = [row for row in exact_name if int(row.bytes or 0) == size]
                match_rows = exact_size
                matched_by = "exact_filename_size"
                identity = canonical_part_identity(name)
                if not match_rows and identity is not None:
                    match_rows = [
                        row for row in by_identity.get(identity, []) if int(row.bytes or 0) == size
                    ]
                    matched_by = "canonical_part_identity_size"
                free = [
                    row for row in match_rows if (int(row.channel_id), int(row.message_id)) not in used
                ]
                if len(match_rows) == 1 and len(free) == 1:
                    row = free[0]
                    db.add(
                        TelegramVideoPartBinding(
                            part_id=part.id,
                            channel_id=int(row.channel_id),
                            message_id=int(row.message_id),
                            matched_by=matched_by,
                        )
                    )
                    used.add((int(row.channel_id), int(row.message_id)))
                    stats["bound"] += 1
                elif len(match_rows) > 1 or (match_rows and not free):
                    stats["conflict"] += 1
                elif exact_name or (identity is not None and by_identity.get(identity)):
                    stats["size_mismatch"] += 1
                else:
                    stats["missing"] += 1
            await db.commit()
            return stats

    async def part_bindings(self, channel_id: int, *, session_id: str | None = None) -> list[dict[str, Any]]:
        async with SessionLocal() as db:
            query = (
                select(VideoPart, TelegramVideoPartBinding, TelegramChannelFile)
                .outerjoin(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                .outerjoin(
                    TelegramChannelFile,
                    (TelegramChannelFile.channel_id == TelegramVideoPartBinding.channel_id)
                    & (TelegramChannelFile.message_id == TelegramVideoPartBinding.message_id),
                )
                .where(VideoPart.status == "ready")
                .order_by(VideoPart.created_at, VideoPart.part_no)
            )
            if session_id:
                import uuid

                try:
                    parsed = uuid.UUID(session_id)
                except ValueError:
                    return []
                query = query.where(VideoPart.video_session_id == parsed)
            rows = (await db.execute(query)).all()

            used_message_ids = {
                int(message_id)
                for (message_id,) in (
                    await db.execute(
                        select(TelegramVideoPartBinding.message_id).where(
                            TelegramVideoPartBinding.channel_id == channel_id
                        )
                    )
                ).all()
            }

            result: list[dict[str, Any]] = []
            for part, binding, tg_file in rows:
                part_bytes = int(part.final_bytes or part.expected_bytes or 0)
                payload: dict[str, Any] = {
                    "part_id": str(part.id),
                    "session_id": str(part.video_session_id),
                    "part_no": int(part.part_no),
                    "file_name": part.file_name,
                    "part_bytes": part_bytes,
                    "status": "missing",
                    "channel_id": int(binding.channel_id) if binding else None,
                    "message_id": int(binding.message_id) if binding else None,
                    "matched_by": binding.matched_by if binding else None,
                    "linked_at_utc": _iso(binding.linked_at_utc) if binding else None,
                    "telegram_bytes": int(tg_file.bytes) if tg_file else None,
                    "mime_type": tg_file.mime_type if tg_file else None,
                    "document_id": int(tg_file.document_id) if tg_file else None,
                    "message_date_utc": _iso(tg_file.message_date_utc) if tg_file else None,
                    "candidate_count": 0,
                    "same_name_candidates": [],
                }
                if binding is not None:
                    payload["status"] = "linked" if int(binding.channel_id) == channel_id else "linked_other_channel"
                    result.append(payload)
                    continue

                same_name = (
                    await db.execute(
                        select(TelegramChannelFile)
                        .where(
                            TelegramChannelFile.channel_id == channel_id,
                            TelegramChannelFile.file_name == part.file_name,
                        )
                        .order_by(TelegramChannelFile.message_id)
                        .limit(20)
                    )
                ).scalars().all()
                payload["same_name_candidates"] = [
                    {
                        "message_id": int(item.message_id),
                        "bytes": int(item.bytes),
                        "mime_type": item.mime_type,
                        "used": int(item.message_id) in used_message_ids,
                    }
                    for item in same_name
                ]
                exact = [item for item in same_name if int(item.bytes) == part_bytes]
                payload["candidate_count"] = len(exact)
                if len(exact) > 1:
                    payload["status"] = "conflict"
                elif len(exact) == 1:
                    payload["status"] = "conflict" if int(exact[0].message_id) in used_message_ids else "match_pending"
                elif same_name:
                    payload["status"] = "size_mismatch"
                result.append(payload)
            return result

    async def status(self, channel_id: int) -> dict[str, Any]:
        async with SessionLocal() as db:
            state = await db.get(TelegramChannelState, channel_id)
            file_count = int(
                await db.scalar(
                    select(func.count()).select_from(TelegramChannelFile).where(
                        TelegramChannelFile.channel_id == channel_id
                    )
                )
                or 0
            )
            bound_count = int(
                await db.scalar(
                    select(func.count()).select_from(TelegramVideoPartBinding).where(
                        TelegramVideoPartBinding.channel_id == channel_id
                    )
                )
                or 0
            )
            ready_count = int(
                await db.scalar(
                    select(func.count()).select_from(VideoPart).where(VideoPart.status == "ready")
                )
                or 0
            )
            return {
                "channel_id": channel_id,
                "channel_title": state.channel_title if state else None,
                "account_id": int(state.account_id) if state and state.account_id is not None else None,
                "account_display": state.account_display if state else None,
                "last_scanned_message_id": int(state.last_scanned_message_id or 0) if state else 0,
                "last_scan_at_utc": _iso(state.last_scan_at_utc) if state else None,
                "last_error": state.last_error if state else None,
                "catalog_files": file_count,
                "bound_parts": bound_count,
                "ready_parts": ready_count,
            }
