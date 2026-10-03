from __future__ import annotations

import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

import qrcode
from telethon import TelegramClient, errors, utils


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
STATE_ROOT = PROJECT_ROOT / "telegram_state"
QR_PATH = STATE_ROOT / "telegram-login-qr.png"


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"missing {path}; create .env first")
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key.strip()] = value
    return result


def require_config(env: dict[str, str]) -> tuple[int, str, str, int | None]:
    session_name = (env.get("TELEGRAM_SESSION_NAME") or env.get("SESSION_NAME") or "").strip()
    required = {
        "TELEGRAM_API_ID": (env.get("TELEGRAM_API_ID") or "").strip(),
        "TELEGRAM_API_HASH": (env.get("TELEGRAM_API_HASH") or "").strip(),
        "SESSION_NAME": session_name,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("not configured: " + ", ".join(missing))
    try:
        api_id = int(required["TELEGRAM_API_ID"])
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be numeric") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_name):
        raise RuntimeError("SESSION_NAME may contain only A-Z, a-z, 0-9, _ and -")
    raw_channel = (env.get("TELEGRAM_CHANNEL_ID") or "").strip()
    channel_id = int(raw_channel) if raw_channel else None
    return api_id, required["TELEGRAM_API_HASH"], session_name, channel_id


def show_qr(url: str) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    code = qrcode.QRCode(border=2)
    code.add_data(url)
    code.make(fit=True)
    print("\nScan in Telegram: Settings -> Devices -> Link Desktop Device\n")
    try:
        code.print_ascii(invert=True)
    except Exception:
        pass
    image = code.make_image(fill_color="black", back_color="white")
    image.save(QR_PATH)
    print(f"\nQR image: {QR_PATH}")
    if os.name == "nt":
        try:
            os.startfile(QR_PATH)  # type: ignore[attr-defined]
        except OSError:
            pass


async def resolve_channel(client: TelegramClient, configured: int):
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        try:
            peer_id = int(utils.get_peer_id(entity))
            raw_id = int(getattr(entity, "id", 0) or 0)
        except Exception:
            continue
        if peer_id == configured or raw_id == configured:
            return entity
    return await client.get_entity(configured)


async def main() -> int:
    env = load_dotenv(ENV_PATH)
    api_id, api_hash, session_name, channel_id = require_config(env)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    session_base = STATE_ROOT / session_name
    client = TelegramClient(str(session_base), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Starting Telegram user QR login.")
            while not await client.is_user_authorized():
                qr = await client.qr_login()
                show_qr(qr.url)
                try:
                    await qr.wait()
                except asyncio.TimeoutError:
                    print("QR expired; creating another one...")
                    continue
                except errors.SessionPasswordNeededError:
                    await client.sign_in(password=getpass.getpass("Telegram 2FA password: "))
                    break
        me = await client.get_me()
        if me is None:
            raise RuntimeError("Telegram did not return the authorized user")
        print(f"Authorized user id={me.id} username=@{getattr(me, 'username', '') or '-'}")
        print(f"Session: {session_base}.session")

        if channel_id is not None:
            channel = await resolve_channel(client, channel_id)
            normalized = int(utils.get_peer_id(channel))
            title = getattr(channel, "title", None) or getattr(channel, "username", None) or normalized
            print(f"Channel: {title} | peer_id={normalized}")
            if normalized != channel_id:
                print(f"Set TELEGRAM_CHANNEL_ID={normalized} in .env")
        else:
            print("\nTELEGRAM_CHANNEL_ID is empty. Available channels/supergroups:")
            shown = 0
            async for dialog in client.iter_dialogs():
                try:
                    peer_id = int(utils.get_peer_id(dialog.entity))
                except Exception:
                    continue
                if peer_id > -1000000000000:
                    continue
                title = getattr(dialog.entity, "title", None) or dialog.name
                print(f"  {peer_id}  {title}")
                shown += 1
                if shown >= 100:
                    break
            print("Copy the required peer_id to TELEGRAM_CHANNEL_ID in .env.")
        return 0
    finally:
        await client.disconnect()
        try:
            QR_PATH.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
