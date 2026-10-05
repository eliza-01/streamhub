from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from streamhub_common.logging import configure_logging
from streamhub_common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("streamhub.registration_bot")


class TelegramBotError(RuntimeError):
    pass


class RegistrationBot:
    def __init__(self) -> None:
        token = (settings.telegram_registration_bot_token or "").strip()
        username = (settings.telegram_registration_bot_username or "").strip().lstrip("@")
        if not token:
            raise RuntimeError("TELEGRAM_REGISTRATION_BOT_TOKEN is required")
        if not username:
            raise RuntimeError("TELEGRAM_REGISTRATION_BOT_USERNAME is required")
        self.username = username
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.api_base_url = "http://api:8000"
        self.offset = 0
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def telegram(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self.client.post(f"{self.base_url}/{method}", json=payload or {})
        except httpx.HTTPError as exc:
            raise TelegramBotError(f"Telegram {method}: transport error") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramBotError(f"Telegram {method}: invalid JSON ({response.status_code})") from exc
        if response.status_code >= 400 or not data.get("ok"):
            raise TelegramBotError(f"Telegram {method}: {data.get('description') or response.text[:300]}")
        return data.get("result")

    async def streamhub(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.api_base_url}{path}",
            json=payload,
            headers={"X-Internal-Service-Token": settings.internal_service_token},
        )
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:300]}
        if response.status_code >= 400:
            raise TelegramBotError(str(data.get("detail") or f"StreamHub HTTP {response.status_code}"))
        return data

    async def send_registration_button(self, chat_id: int, registration_id: str, nickname: str) -> None:
        await self.telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"Регистрация MRW Hub для {nickname}.\nНажмите кнопку ниже, чтобы подтвердить регистрацию.",
                "reply_markup": {
                    "inline_keyboard": [[{
                        "text": "Подтвердить регистрацию",
                        "callback_data": f"confirm:{registration_id}",
                    }]]
                },
            },
        )

    async def send_message(self, chat_id: int, text: str) -> None:
        await self.telegram("sendMessage", {"chat_id": chat_id, "text": text})

    async def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = int(chat.get("id") or 0)
        telegram_user_id = int(sender.get("id") or 0)
        if chat_id == 0 or telegram_user_id <= 0:
            return
        if chat.get("type") != "private":
            return
        if not text.startswith("/start"):
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await self.send_message(chat_id, "Откройте регистрацию на сайте MRW Hub и нажмите «Подтвердить регистрацию».")
            return

        start_token = parts[1].strip()
        try:
            data = await self.streamhub(
                "/api/v1/user-auth/internal/telegram/start",
                {
                    "start_token": start_token,
                    "telegram_user_id": telegram_user_id,
                    "telegram_chat_id": chat_id,
                    "telegram_username": sender.get("username"),
                    "telegram_first_name": sender.get("first_name"),
                },
            )
        except TelegramBotError as exc:
            await self.send_message(chat_id, str(exc))
            return

        if data.get("confirmed"):
            await self.send_message(chat_id, "Регистрация подтверждена ✅")
            return
        await self.send_registration_button(chat_id, str(data["registration_id"]), str(data.get("nickname") or "MRW Hub"))

    async def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        sender = callback.get("from") or {}
        telegram_user_id = int(sender.get("id") or 0)
        data = str(callback.get("data") or "")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        message_id = int(message.get("message_id") or 0)
        if not callback_id or telegram_user_id <= 0 or not data.startswith("confirm:"):
            return

        registration_id = data.removeprefix("confirm:").strip()
        try:
            result = await self.streamhub(
                "/api/v1/user-auth/internal/telegram/confirm",
                {"registration_id": registration_id, "telegram_user_id": telegram_user_id},
            )
        except TelegramBotError as exc:
            await self.telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(exc)[:180], "show_alert": True})
            return

        await self.telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Регистрация подтверждена"})
        if chat_id and message_id:
            await self.telegram(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": "Регистрация подтверждена ✅",
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        logger.info("telegram registration confirmed user=%s registration=%s", telegram_user_id, registration_id)

    async def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("message"), dict):
            await self.handle_message(update["message"])
        if isinstance(update.get("callback_query"), dict):
            await self.handle_callback(update["callback_query"])

    async def run(self) -> None:
        await self.telegram("deleteWebhook", {"drop_pending_updates": False})
        me = await self.telegram("getMe")
        actual_username = str((me or {}).get("username") or "")
        if actual_username and actual_username.lower() != self.username.lower():
            logger.warning(
                "TELEGRAM_REGISTRATION_BOT_USERNAME=%s but Bot API returned @%s",
                self.username,
                actual_username,
            )
        logger.info("registration bot started @%s", actual_username or self.username)
        while True:
            try:
                updates = await self.telegram(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": settings.telegram_registration_poll_timeout_seconds,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in updates or []:
                    update_id = int(update.get("update_id") or 0)
                    if update_id >= self.offset:
                        self.offset = update_id + 1
                    try:
                        await self.handle_update(update)
                    except Exception:
                        logger.exception("failed to process Telegram update_id=%s", update_id)
            except TelegramBotError as exc:
                logger.warning("Telegram polling failed; retrying: %s", exc)
                await asyncio.sleep(3)


async def main() -> None:
    bot = RegistrationBot()
    try:
        await bot.run()
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
