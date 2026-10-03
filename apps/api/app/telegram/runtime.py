from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from streamhub_common.settings import get_settings

from .registry import TelegramRegistry
from .storage import (
    TelegramAuthRequired,
    TelegramConfig,
    TelegramFileRecord,
    TelegramMediaReader,
    TelegramStorageError,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_payload(item: TelegramFileRecord) -> dict[str, Any]:
    return {
        "message_id": int(item.message_id),
        "file_name": item.file_name,
        "bytes": int(item.bytes),
        "mime_type": item.mime_type,
        "document_id": int(item.document_id),
        "message_date_utc": item.message_date_utc.isoformat() + "Z" if item.message_date_utc else None,
    }


class TelegramRuntime:
    """Application owner of the one read-only Telegram user connection."""

    def __init__(self) -> None:
        settings = get_settings()
        self.registry = TelegramRegistry()
        self.sync_interval_seconds = settings.telegram_sync_interval_seconds
        self.max_parallel_downloads = settings.telegram_max_parallel_downloads
        self.config: TelegramConfig | None = None
        self.reader: TelegramMediaReader | None = None
        self._sync_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._runtime: dict[str, Any] = {
            "state": "not_configured",
            "last_error": None,
            "last_sync_at_utc": None,
            "last_scanned_messages": 0,
            "last_discovered_files": 0,
        }

    def _set(self, **fields: Any) -> None:
        self._runtime.update(fields)

    async def start(self) -> None:
        try:
            config = TelegramConfig.from_settings(get_settings())
        except TelegramStorageError as exc:
            self._set(state="not_configured", last_error=str(exc))
            return
        config.state_root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.reader = TelegramMediaReader(config, max_parallel_downloads=self.max_parallel_downloads)
        self._set(state="idle", last_error=None)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="telegram-readonly-sync")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.reader is not None:
            await self.reader.close()

    async def _loop(self) -> None:
        await asyncio.sleep(3)
        while True:
            try:
                await self.sync_once(reconcile_missing=False)
            except Exception:
                pass
            await asyncio.sleep(self.sync_interval_seconds)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._runtime)

    async def status(self) -> dict[str, Any]:
        if self.config is None:
            return {
                "ok": True,
                "configured": False,
                "config_error": self._runtime.get("last_error"),
                "runtime": self.snapshot(),
            }
        return {
            "ok": True,
            "configured": True,
            "session_name": self.config.session_name,
            "session_present": self.config.session_file.is_file(),
            "channel": await self.registry.status(self.config.channel_id),
            "runtime": self.snapshot(),
        }

    def require_reader(self) -> TelegramMediaReader:
        if self.config is None or self.reader is None:
            raise TelegramStorageError(self._runtime.get("last_error") or "Telegram is not configured")
        return self.reader

    async def sync_once(self, *, reconcile_missing: bool = False) -> dict[str, Any]:
        config = self.config
        reader = self.require_reader()
        assert config is not None
        async with self._sync_lock:
            self._set(state="syncing", last_error=None)
            min_message_id = await self.registry.last_message_id(config.channel_id)
            try:
                scan = await reader.scan(min_message_id=min_message_id)
            except Exception as exc:
                try:
                    await self.registry.record_error(config.channel_id, str(exc))
                except Exception:
                    pass
                self._set(
                    state="auth_required" if isinstance(exc, TelegramAuthRequired) else "error",
                    last_error=str(exc),
                )
                raise

            if scan.channel_id != config.channel_id:
                error = f"configured channel={config.channel_id}, Telegram returned channel={scan.channel_id}"
                await self.registry.record_error(config.channel_id, error)
                self._set(state="error", last_error=error)
                raise TelegramStorageError(error)

            await self.registry.store_scan(scan)
            first_matches = await self.registry.match_ready_parts(scan.channel_id)
            reconcile_result = None
            reconcile_scan = None
            unresolved_names: list[str] = []
            final_matches = dict(first_matches)
            total_bound = int(first_matches.get("bound", 0) or 0)
            if reconcile_missing:
                unresolved_names = await self.registry.unresolved_ready_file_names()
                if unresolved_names:
                    reconcile_result = await reader.scan_file_names(set(unresolved_names))
                    reconcile_scan = reconcile_result.scan
                    if reconcile_scan.channel_id != config.channel_id:
                        raise TelegramStorageError(
                            f"reconcile returned channel={reconcile_scan.channel_id}, expected={config.channel_id}"
                        )
                    await self.registry.store_scan(reconcile_scan)
                    second_matches = await self.registry.match_ready_parts(reconcile_scan.channel_id)
                    total_bound += int(second_matches.get("bound", 0) or 0)
                    final_matches = dict(second_matches)
                    final_matches["bound"] = total_bound

            scanned_total = scan.scanned_messages + (reconcile_scan.scanned_messages if reconcile_scan else 0)
            discovered_total = len(scan.files) + (len(reconcile_scan.files) if reconcile_scan else 0)
            self._set(
                state="ready",
                last_error=None,
                last_sync_at_utc=_iso_now(),
                last_scanned_messages=scanned_total,
                last_discovered_files=discovered_total,
            )
            return {
                "channel_id": scan.channel_id,
                "channel_title": scan.channel_title,
                "account_id": scan.account_id,
                "account_display": scan.account_display,
                "from_message_id": min_message_id,
                "highest_message_id": max(
                    scan.highest_message_id,
                    reconcile_scan.highest_message_id if reconcile_scan else 0,
                ),
                "scanned_messages": scanned_total,
                "discovered_files": discovered_total,
                "incremental": {
                    "scanned_messages": scan.scanned_messages,
                    "discovered_files": len(scan.files),
                },
                "reconcile": {
                    "enabled": bool(reconcile_missing),
                    "performed": reconcile_scan is not None,
                    "expected_file_names": len(unresolved_names),
                    "scanned_messages": reconcile_scan.scanned_messages if reconcile_scan else 0,
                    "scanned_documents": reconcile_result.scanned_documents if reconcile_result else 0,
                    "discovered_files": len(reconcile_scan.files) if reconcile_scan else 0,
                    "matched_file_names": list(reconcile_result.matched_file_names) if reconcile_result else [],
                    "near_documents": [_file_payload(x) for x in reconcile_result.near_documents] if reconcile_result else [],
                    "recent_documents": [_file_payload(x) for x in reconcile_result.recent_documents] if reconcile_result else [],
                },
                "matches": final_matches,
            }

    async def bindings(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        if self.config is None:
            raise TelegramStorageError(self._runtime.get("last_error") or "Telegram is not configured")
        return await self.registry.part_bindings(self.config.channel_id, session_id=session_id)


telegram_runtime = TelegramRuntime()
