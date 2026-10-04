from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from telethon import TelegramClient, utils
from telethon.errors import FileReferenceExpiredError
from telethon.tl.types import DocumentAttributeFilename

from streamhub_common.settings import Settings
from streamhub_common.telegram_names import canonical_part_identity, filename_tokens, filenames_equivalent


logger = logging.getLogger(__name__)


_DOWNLOAD_CHUNK = 512 * 1024
_MESSAGE_CACHE_SIZE = 128
_DIAGNOSTIC_RECENT_LIMIT = 40
_DIAGNOSTIC_NEAR_LIMIT = 120


class TelegramStorageError(RuntimeError):
    pass


class TelegramAuthRequired(TelegramStorageError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    channel_id: int
    session_name: str
    state_root: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramConfig":
        raw_api_id = str(settings.telegram_api_id or "").strip()
        api_hash = str(settings.telegram_api_hash or "").strip()
        raw_channel_id = str(settings.telegram_channel_id or "").strip()
        session_name = str(settings.telegram_session_name or "").strip()
        missing = [
            name
            for name, value in (
                ("TELEGRAM_API_ID", raw_api_id),
                ("TELEGRAM_API_HASH", api_hash),
                ("TELEGRAM_CHANNEL_ID", raw_channel_id),
                ("SESSION_NAME", session_name),
            )
            if value is None or value == ""
        ]
        if missing:
            raise TelegramStorageError("not configured: " + ", ".join(missing))
        try:
            api_id = int(raw_api_id)
        except ValueError as exc:
            raise TelegramStorageError("TELEGRAM_API_ID must be numeric") from exc
        try:
            channel_id = int(raw_channel_id)
        except ValueError as exc:
            raise TelegramStorageError("TELEGRAM_CHANNEL_ID must be numeric") from exc
        if api_id <= 0:
            raise TelegramStorageError("TELEGRAM_API_ID must be positive")
        if channel_id >= 0:
            raise TelegramStorageError("TELEGRAM_CHANNEL_ID must be a full channel peer id like -100...")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_name):
            raise TelegramStorageError("TELEGRAM_SESSION_NAME may contain only A-Z, a-z, 0-9, _ and -")
        return cls(
            api_id=api_id,
            api_hash=api_hash,
            channel_id=channel_id,
            session_name=session_name,
            state_root=Path(settings.telegram_state_root).resolve(),
        )

    @property
    def session_base(self) -> Path:
        return self.state_root / self.session_name

    @property
    def session_file(self) -> Path:
        return self.state_root / f"{self.session_name}.session"


@dataclass(frozen=True)
class TelegramFileRecord:
    channel_id: int
    message_id: int
    file_name: str
    bytes: int
    mime_type: str | None
    document_id: int
    message_date_utc: datetime | None


@dataclass(frozen=True)
class TelegramScanResult:
    channel_id: int
    channel_title: str
    account_id: int
    account_display: str
    highest_message_id: int
    scanned_messages: int
    files: tuple[TelegramFileRecord, ...]


@dataclass(frozen=True)
class TelegramReconcileScan:
    scan: TelegramScanResult
    scanned_documents: int
    expected_file_names: tuple[str, ...]
    matched_file_names: tuple[str, ...]
    recent_documents: tuple[TelegramFileRecord, ...]
    near_documents: tuple[TelegramFileRecord, ...]


def _display_name(entity: Any) -> str:
    first = str(getattr(entity, "first_name", "") or "").strip()
    last = str(getattr(entity, "last_name", "") or "").strip()
    username = str(getattr(entity, "username", "") or "").strip()
    name = " ".join(x for x in (first, last) if x)
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    return name or str(getattr(entity, "id", "unknown"))


def _channel_title(entity: Any) -> str:
    return str(getattr(entity, "title", None) or getattr(entity, "username", None) or getattr(entity, "id", "channel"))


def _file_name(message: Any) -> str | None:
    file_obj = getattr(message, "file", None)
    name = str(getattr(file_obj, "name", "") or "").strip()
    if name:
        return name
    document = getattr(message, "document", None)
    for attr in getattr(document, "attributes", None) or ():
        if isinstance(attr, DocumentAttributeFilename):
            value = str(attr.file_name or "").strip()
            if value:
                return value
    return None


class TelegramMediaReader:
    """Single read-only Telethon client shared by indexing and media reads."""

    def __init__(self, config: TelegramConfig, *, max_parallel_downloads: int = 4) -> None:
        self.config = config
        self._client = TelegramClient(str(config.session_base), config.api_id, config.api_hash)
        self._connect_lock = asyncio.Lock()
        self._message_lock = asyncio.Lock()
        self._download_slots = asyncio.Semaphore(max(1, int(max_parallel_downloads)))
        self._channel: Any | None = None
        self._normalized_channel_id: int | None = None
        self._message_cache: OrderedDict[int, Any] = OrderedDict()

    async def _resolve_channel(self) -> Any:
        configured = self.config.channel_id
        async for dialog in self._client.iter_dialogs():
            entity = dialog.entity
            try:
                peer_id = int(utils.get_peer_id(entity))
            except Exception:
                continue
            raw_id = int(getattr(entity, "id", 0) or 0)
            if peer_id == configured or raw_id == configured:
                return entity
        try:
            return await self._client.get_entity(configured)
        except Exception as exc:
            raise TelegramStorageError(f"Telegram channel {configured} is not available to this account") from exc

    async def ensure_connected(self) -> None:
        if self._client.is_connected() and self._channel is not None:
            return
        async with self._connect_lock:
            if not self.config.session_file.is_file():
                raise TelegramAuthRequired(
                    f"Telegram session is missing: {self.config.session_file}. Run .\\telegram-auth.ps1"
                )
            if not self._client.is_connected():
                await self._client.connect()
            if not await self._client.is_user_authorized():
                raise TelegramAuthRequired("Telegram user session is no longer authorized")
            if self._channel is None:
                channel = await self._resolve_channel()
                normalized = int(utils.get_peer_id(channel))
                if normalized != self.config.channel_id:
                    raise TelegramStorageError(
                        f"TELEGRAM_CHANNEL_ID={self.config.channel_id}, Telegram resolved peer_id={normalized}"
                    )
                self._channel = channel
                self._normalized_channel_id = normalized

    async def close(self) -> None:
        self._message_cache.clear()
        self._channel = None
        self._normalized_channel_id = None
        if self._client.is_connected():
            await self._client.disconnect()

    def _record_from_message(self, message: Any) -> TelegramFileRecord | None:
        document = getattr(message, "document", None)
        if document is None:
            return None
        name = _file_name(message)
        if not name:
            return None
        message_date = getattr(message, "date", None)
        if isinstance(message_date, datetime):
            if message_date.tzinfo is None:
                message_date = message_date.replace(tzinfo=timezone.utc)
            message_date = message_date.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            message_date = None
        assert self._normalized_channel_id is not None
        return TelegramFileRecord(
            channel_id=self._normalized_channel_id,
            message_id=int(message.id),
            file_name=name,
            bytes=int(getattr(document, "size", 0) or 0),
            mime_type=str(getattr(document, "mime_type", "") or "") or None,
            document_id=int(getattr(document, "id", 0) or 0),
            message_date_utc=message_date,
        )

    async def _scan_header(self) -> tuple[Any, str]:
        await self.ensure_connected()
        me = await self._client.get_me()
        if me is None:
            raise TelegramAuthRequired("Telegram did not return the authorized user")
        assert self._channel is not None
        return me, _channel_title(self._channel)

    async def scan(self, *, min_message_id: int = 0) -> TelegramScanResult:
        me, channel_title = await self._scan_header()
        assert self._channel is not None
        assert self._normalized_channel_id is not None
        watermark = max(0, int(min_message_id or 0))
        highest = watermark
        scanned = 0
        files: list[TelegramFileRecord] = []
        async for message in self._client.iter_messages(self._channel):
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id <= watermark:
                break
            scanned += 1
            highest = max(highest, message_id)
            record = self._record_from_message(message)
            if record is not None:
                files.append(record)
                self._remember(message)
        return TelegramScanResult(
            channel_id=self._normalized_channel_id,
            channel_title=channel_title,
            account_id=int(me.id),
            account_display=_display_name(me),
            highest_message_id=highest,
            scanned_messages=scanned,
            files=tuple(files),
        )

    async def scan_file_names(self, file_names: set[str], *, max_messages: int = 50000) -> TelegramReconcileScan:
        wanted = {str(name).strip() for name in file_names if str(name).strip()}
        wanted_tokens: set[str] = set()
        wanted_identities = {
            identity for name in wanted if (identity := canonical_part_identity(name)) is not None
        }
        for name in wanted:
            wanted_tokens.update(filename_tokens(name))
        me, channel_title = await self._scan_header()
        assert self._channel is not None
        assert self._normalized_channel_id is not None
        if not wanted:
            empty = TelegramScanResult(
                self._normalized_channel_id, channel_title, int(me.id), _display_name(me), 0, 0, ()
            )
            return TelegramReconcileScan(empty, 0, (), (), (), ())

        highest = scanned = scanned_documents = 0
        exact_files: list[TelegramFileRecord] = []
        recent_documents: list[TelegramFileRecord] = []
        near_documents: list[TelegramFileRecord] = []
        matched_names: set[str] = set()
        async for message in self._client.iter_messages(self._channel):
            scanned += 1
            highest = max(highest, int(getattr(message, "id", 0) or 0))
            record = self._record_from_message(message)
            if record is not None:
                scanned_documents += 1
                if len(recent_documents) < _DIAGNOSTIC_RECENT_LIMIT:
                    recent_documents.append(record)
                record_identity = canonical_part_identity(record.file_name)
                is_exact = record.file_name in wanted
                is_canonical = record_identity is not None and record_identity in wanted_identities
                if is_exact or is_canonical:
                    exact_files.append(record)
                    for expected in wanted:
                        if filenames_equivalent(expected, record.file_name):
                            matched_names.add(expected)
                    self._remember(message)
                elif (
                    len(near_documents) < _DIAGNOSTIC_NEAR_LIMIT
                    and wanted_tokens
                    and filename_tokens(record.file_name) & wanted_tokens
                ):
                    near_documents.append(record)
            if scanned >= max(1, int(max_messages)):
                break
        scan = TelegramScanResult(
            self._normalized_channel_id,
            channel_title,
            int(me.id),
            _display_name(me),
            highest,
            scanned,
            tuple(exact_files),
        )
        return TelegramReconcileScan(
            scan,
            scanned_documents,
            tuple(sorted(wanted)),
            tuple(sorted(matched_names)),
            tuple(recent_documents),
            tuple(near_documents),
        )

    def _remember(self, message: Any) -> None:
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id <= 0:
            return
        self._message_cache[message_id] = message
        self._message_cache.move_to_end(message_id)
        while len(self._message_cache) > _MESSAGE_CACHE_SIZE:
            self._message_cache.popitem(last=False)

    async def _message(self, message_id: int, *, refresh: bool = False) -> Any:
        await self.ensure_connected()
        key = int(message_id)
        if not refresh:
            cached = self._message_cache.get(key)
            if cached is not None:
                self._message_cache.move_to_end(key)
                return cached
        async with self._message_lock:
            if refresh:
                self._message_cache.pop(key, None)
            else:
                cached = self._message_cache.get(key)
                if cached is not None:
                    self._message_cache.move_to_end(key)
                    return cached
            assert self._channel is not None
            message = await self._client.get_messages(self._channel, ids=key)
            if message is None or getattr(message, "document", None) is None:
                raise TelegramStorageError(f"Telegram message {message_id} does not contain a document")
            self._remember(message)
            return message

    async def validate_document(
        self,
        message_id: int,
        *,
        expected_name: str,
        expected_size: int,
        refresh: bool = False,
    ) -> Any:
        message = await self._message(message_id, refresh=refresh)
        document = message.document
        actual_name = _file_name(message)
        actual_size = int(getattr(document, "size", 0) or 0)
        if not filenames_equivalent(expected_name, actual_name or ""):
            raise TelegramStorageError(
                f"Telegram message {message_id}: filename no longer matches part: {actual_name!r} != {expected_name!r}"
            )
        if actual_size != int(expected_size):
            raise TelegramStorageError(
                f"Telegram message {message_id}: size changed: {actual_size} != {expected_size}"
            )
        return document

    async def iter_document_range(
        self,
        message_id: int,
        *,
        expected_name: str,
        expected_size: int,
        offset: int,
        length: int,
    ) -> AsyncIterator[bytes]:
        if offset < 0 or length <= 0 or offset + length > int(expected_size):
            raise TelegramStorageError(
                f"invalid byte range offset={offset} length={length} size={expected_size}"
            )
        async with self._download_slots:
            remaining = int(length)
            current_offset = int(offset)
            refreshed_reference = False

            while remaining > 0:
                document = await self.validate_document(
                    message_id,
                    expected_name=expected_name,
                    expected_size=expected_size,
                    refresh=refreshed_reference,
                )
                stream = self._client.iter_download(
                    document,
                    offset=current_offset,
                    chunk_size=_DOWNLOAD_CHUNK,
                    request_size=_DOWNLOAD_CHUNK,
                    file_size=int(expected_size),
                )
                try:
                    async for chunk in stream:
                        if remaining <= 0:
                            break
                        data = bytes(chunk)
                        if not data:
                            continue
                        if len(data) > remaining:
                            data = data[:remaining]
                        remaining -= len(data)
                        current_offset += len(data)
                        yield data
                        if remaining == 0:
                            break
                except FileReferenceExpiredError as exc:
                    if refreshed_reference:
                        raise TelegramStorageError(
                            f"Telegram file reference expired again after refresh: message={message_id}"
                        ) from exc
                    logger.info(
                        "Telegram file reference expired; refreshing message=%s offset=%s remaining=%s",
                        message_id,
                        current_offset,
                        remaining,
                    )
                    refreshed_reference = True
                    continue
                finally:
                    await stream.close()

                break

            if remaining != 0:
                raise TelegramStorageError(
                    f"Telegram download ended early: message={message_id}, remaining={remaining} bytes"
                )
