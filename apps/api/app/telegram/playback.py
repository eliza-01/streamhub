from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from sqlalchemy import select

from streamhub_common.db import SessionLocal
from streamhub_common.models import (
    TelegramVideoPartBinding,
    VideoPart,
    VideoPartSegment,
    VideoSegment,
    VideoSession,
)

from .runtime import TelegramRuntime


class PlaybackNotFound(RuntimeError):
    pass


class PlaybackUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.total}"


@dataclass(frozen=True)
class StreamPlan:
    status_code: int
    media_type: str
    content_length: int
    content_range: str | None
    iterator: AsyncIterator[bytes]


def parse_single_range(value: str | None, total: int) -> ByteRange | None:
    if value is None or not value.strip():
        return None
    if total <= 0:
        raise PlaybackUnavailable("resource is empty")
    text = value.strip()
    if not text.lower().startswith("bytes="):
        raise PlaybackUnavailable("only bytes ranges are supported")
    spec = text[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        raise PlaybackUnavailable("exactly one byte range is supported")
    left, right = (part.strip() for part in spec.split("-", 1))
    try:
        if left:
            start = int(left)
            if start < 0 or start >= total:
                raise PlaybackUnavailable("range start is outside the resource")
            if right:
                end = int(right)
                if end < start:
                    raise PlaybackUnavailable("range end precedes start")
                end = min(end, total - 1)
            else:
                end = total - 1
        else:
            if not right:
                raise PlaybackUnavailable("invalid suffix range")
            suffix = int(right)
            if suffix <= 0:
                raise PlaybackUnavailable("suffix length must be positive")
            suffix = min(suffix, total)
            start = total - suffix
            end = total - 1
    except ValueError as exc:
        raise PlaybackUnavailable("invalid byte range") from exc
    return ByteRange(start=start, end=end, total=total)


def _validate_contiguous(segments: list[dict[str, Any]]) -> None:
    if not segments:
        raise PlaybackUnavailable("video session has no Telegram-linked ready parts")
    previous = int(segments[0]["segment_no"])
    for row in segments[1:]:
        current = int(row["segment_no"])
        if current != previous + 1:
            raise PlaybackUnavailable(
                f"Telegram-linked parts have a gap: segment {previous} is followed by {current}"
            )
        previous = current


def _playlist(session_id: uuid.UUID, segments: list[dict[str, Any]]) -> str:
    _validate_contiguous(segments)
    max_duration = max(float(row["duration_ms"]) / 1000.0 for row in segments)
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{max(1, math.ceil(max_duration))}",
        f"#EXT-X-MEDIA-SEQUENCE:{int(segments[0]['segment_no'])}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    previous_run: int | None = None
    for row in segments:
        run_no = int(row["run_no"])
        if previous_run is not None and run_no != previous_run:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{max(0.001, float(row['duration_ms']) / 1000.0):.3f},")
        lines.append(
            f"/api/v1/playback/segments/{row['part_id']}/{int(row['segment_no'])}.ts"
            f"?session_id={session_id}"
        )
        previous_run = run_no
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class TelegramPlaybackService:
    def __init__(self, runtime: TelegramRuntime) -> None:
        self.runtime = runtime

    async def linked_segments(self, session_id: uuid.UUID) -> list[dict[str, Any]]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(
                        VideoPart.id.label("part_id"),
                        VideoPart.part_no,
                        VideoPart.run_no,
                        VideoPart.file_name.label("part_file_name"),
                        VideoPart.expected_bytes,
                        VideoPart.final_bytes,
                        TelegramVideoPartBinding.channel_id,
                        TelegramVideoPartBinding.message_id,
                        VideoPartSegment.segment_no,
                        VideoPartSegment.expected_bytes.label("segment_bytes"),
                        VideoPartSegment.part_offset_bytes,
                        VideoSegment.duration_ms,
                    )
                    .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                    .join(VideoPartSegment, VideoPartSegment.part_id == VideoPart.id)
                    .join(VideoSegment, VideoSegment.id == VideoPartSegment.segment_id)
                    .where(VideoPart.video_session_id == session_id, VideoPart.status == "ready", VideoPart.kind == "source")
                    .order_by(VideoPartSegment.segment_no, VideoPart.part_no)
                )
            ).mappings().all()
            return [
                {
                    **dict(row),
                    "part_id": str(row["part_id"]),
                    "part_bytes": int(row["final_bytes"] or row["expected_bytes"] or 0),
                }
                for row in rows
            ]

    async def session_status(self, session_id: uuid.UUID) -> dict[str, Any]:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, session_id)
            if session is None or session.deleted_at_utc is not None:
                raise PlaybackNotFound("video session not found")
        segments = await self.linked_segments(session_id)
        error = None
        try:
            _validate_contiguous(segments)
        except PlaybackUnavailable as exc:
            error = str(exc)
        return {
            "video_session_id": str(session_id),
            "playable": error is None,
            "playback_error": error,
            "linked_segment_count": len(segments),
            "first_segment_no": int(segments[0]["segment_no"]) if segments else None,
            "last_segment_no": int(segments[-1]["segment_no"]) if segments else None,
            "playlist_url": f"/api/v1/playback/video-sessions/{session_id}/index.m3u8",
        }

    async def playlist(self, session_id: uuid.UUID) -> str:
        async with SessionLocal() as db:
            session = await db.get(VideoSession, session_id)
            if session is None or session.deleted_at_utc is not None:
                raise PlaybackNotFound("video session not found")
        return _playlist(session_id, await self.linked_segments(session_id))

    def _reader_for_channel(self, channel_id: int):
        reader = self.runtime.require_reader()
        if int(reader.config.channel_id) != int(channel_id):
            raise PlaybackUnavailable(
                f"part is linked to Telegram channel {channel_id}, configured channel is {reader.config.channel_id}"
            )
        return reader

    @staticmethod
    def _range_or_full(range_header: str | None, total: int) -> tuple[ByteRange, int]:
        requested = parse_single_range(range_header, total)
        if requested is None:
            requested = ByteRange(start=0, end=total - 1, total=total)
            return requested, 200
        return requested, 206

    async def segment_stream(
        self, part_id: uuid.UUID, segment_no: int, range_header: str | None
    ) -> StreamPlan:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(
                        VideoPart.file_name.label("part_file_name"),
                        VideoPart.expected_bytes,
                        VideoPart.final_bytes,
                        TelegramVideoPartBinding.channel_id,
                        TelegramVideoPartBinding.message_id,
                        VideoPartSegment.expected_bytes.label("segment_bytes"),
                        VideoPartSegment.part_offset_bytes,
                    )
                    .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                    .join(VideoPartSegment, VideoPartSegment.part_id == VideoPart.id)
                    .where(
                        VideoPart.id == part_id,
                        VideoPart.status == "ready",
                        VideoPart.kind == "source",
                        VideoPartSegment.segment_no == segment_no,
                    )
                )
            ).mappings().first()
        if row is None:
            raise PlaybackNotFound("playback segment not found or part is not Telegram-linked")
        total = int(row["segment_bytes"])
        requested, status = self._range_or_full(range_header, total)
        part_bytes = int(row["final_bytes"] or row["expected_bytes"] or 0)
        reader = self._reader_for_channel(int(row["channel_id"]))
        iterator = reader.iter_document_range(
            int(row["message_id"]),
            expected_name=str(row["part_file_name"]),
            expected_size=part_bytes,
            offset=int(row["part_offset_bytes"]) + requested.start,
            length=requested.length,
        )
        return StreamPlan(
            status_code=status,
            media_type="video/mp2t",
            content_length=requested.length,
            content_range=requested.content_range if status == 206 else None,
            iterator=iterator,
        )

    async def part_stream(self, part_id: uuid.UUID, range_header: str | None) -> StreamPlan:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(
                        VideoPart.file_name.label("part_file_name"),
                        VideoPart.expected_bytes,
                        VideoPart.final_bytes,
                        TelegramVideoPartBinding.channel_id,
                        TelegramVideoPartBinding.message_id,
                    )
                    .join(TelegramVideoPartBinding, TelegramVideoPartBinding.part_id == VideoPart.id)
                    .where(VideoPart.id == part_id, VideoPart.status == "ready", VideoPart.kind == "source")
                )
            ).mappings().first()
        if row is None:
            raise PlaybackNotFound("video part not found or is not Telegram-linked")
        total = int(row["final_bytes"] or row["expected_bytes"] or 0)
        requested, status = self._range_or_full(range_header, total)
        reader = self._reader_for_channel(int(row["channel_id"]))
        iterator = reader.iter_document_range(
            int(row["message_id"]),
            expected_name=str(row["part_file_name"]),
            expected_size=total,
            offset=requested.start,
            length=requested.length,
        )
        return StreamPlan(
            status_code=status,
            media_type="video/mp2t",
            content_length=requested.length,
            content_range=requested.content_range if status == 206 else None,
            iterator=iterator,
        )
