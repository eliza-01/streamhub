from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from streamhub_common.db import get_db
from streamhub_common.models import User, UserTelegramRegistration, UserTwitchRegistration
from streamhub_common.settings import get_settings

router = APIRouter(prefix="/api/v1/user-auth", tags=["user-auth"])
settings = get_settings()

_LOGIN_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 600_000
_MAX_AVATAR_BYTES = 10 * 1024 * 1024
_MAX_AVATAR_PIXELS = 12_000_000
_AVATAR_SIZE = 512


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_login(value: str) -> str:
    value = value.strip().lower()
    if not _LOGIN_RE.fullmatch(value):
        raise HTTPException(400, "Логин: 3–32 символа, только латиница, цифры, ., _ и -")
    return value


def _validate_nickname(value: str) -> str:
    value = value.strip()
    if len(value) < 2 or len(value) > 40:
        raise HTTPException(400, "Ник должен содержать от 2 до 40 символов")
    return value


def _validate_password(password: str, password_confirm: str | None = None) -> str:
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(400, "Пароль должен содержать от 8 до 128 символов")
    if password_confirm is not None and password != password_confirm:
        raise HTTPException(400, "Пароли не совпадают")
    return password


def _password_hash_sync(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return f"{_PASSWORD_SCHEME}${_PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_verify_sync(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_raw, salt_hex, digest_hex = encoded.split("$", 3)
        if scheme != _PASSWORD_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_raw),
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (TypeError, ValueError):
        return False


def _registration_token_hash(kind: str, token: str) -> str:
    payload = f"telegram-registration:{kind}:{token}".encode("utf-8")
    return hmac.new(settings.session_hmac_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _twitch_registration_token_hash(kind: str, token: str) -> str:
    payload = f"twitch-registration:{kind}:{token}".encode("utf-8")
    return hmac.new(settings.session_hmac_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _twitch_registration_redirect_uri() -> str:
    configured = (settings.twitch_registration_redirect_uri or "").strip()
    if configured:
        return configured
    return f"{settings.public_api_base_url.rstrip('/')}/api/v1/user-auth/twitch-registration/callback"


def _require_twitch_registration_config() -> None:
    if not settings.twitch_client_id or settings.twitch_client_id == "CHANGE_ME":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Twitch-регистрация не настроена: задайте TWITCH_CLIENT_ID")
    if not settings.twitch_client_secret or settings.twitch_client_secret == "CHANGE_ME":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Twitch-регистрация не настроена: задайте TWITCH_CLIENT_SECRET")


def _bot_username() -> str:
    value = (settings.telegram_registration_bot_username or "").strip().lstrip("@")
    if not value:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Telegram-регистрация не настроена: задайте TELEGRAM_REGISTRATION_BOT_USERNAME",
        )
    return value


def _user_is_verified(user: User) -> bool:
    # Keep already-confirmed email accounts usable while new registrations use Telegram or Twitch.
    return (
        user.telegram_verified_at_utc is not None
        or user.twitch_verified_at_utc is not None
        or user.email_verified_at_utc is not None
    )


def _user_payload(user: User) -> dict:
    avatar_url = None
    if user.avatar_path:
        version = int(user.updated_at.timestamp()) if user.updated_at else 0
        avatar_url = f"/api/v1/user-auth/users/{user.id}/avatar?v={version}"
    return {
        "id": str(user.id),
        "nickname": user.nickname,
        "login": user.login,
        "email": user.email,
        "email_verified": user.email_verified_at_utc is not None,
        "telegram_verified": user.telegram_verified_at_utc is not None,
        "telegram_username": user.telegram_username,
        "twitch_verified": user.twitch_verified_at_utc is not None,
        "twitch_login": user.twitch_login,
        "avatar_url": avatar_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _jwt_token(user: User, token_type: str, ttl_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user.id),
            "type": token_type,
            "login": user.login,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _set_auth_cookies(response: Response, user: User) -> None:
    common = {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(
        settings.auth_access_cookie_name,
        _jwt_token(user, "access", settings.jwt_access_ttl_seconds),
        max_age=settings.jwt_access_ttl_seconds,
        **common,
    )
    response.set_cookie(
        settings.auth_refresh_cookie_name,
        _jwt_token(user, "refresh", settings.jwt_refresh_ttl_seconds),
        max_age=settings.jwt_refresh_ttl_seconds,
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.auth_access_cookie_name, path="/", samesite="lax")
    response.delete_cookie(settings.auth_refresh_cookie_name, path="/", samesite="lax")


def _decode_token(token: str | None, expected_type: str) -> uuid.UUID:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != expected_type:
            raise ValueError("wrong token type")
        return uuid.UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла") from exc


async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if not user or not user.is_active or not _user_is_verified(user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь недоступен")
    return user


async def require_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user_id = _decode_token(request.cookies.get(settings.auth_access_cookie_name), "access")
    return await _load_user(db, user_id)


def _require_internal_token(value: str | None) -> None:
    expected = settings.internal_service_token.encode("utf-8")
    actual = (value or "").encode("utf-8")
    if not actual or not hmac.compare_digest(actual, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal service token")


async def _save_avatar(user_id: uuid.UUID, upload: UploadFile | None) -> str | None:
    if upload is None or not upload.filename:
        return None
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(400, "Аватар должен быть изображением")
    raw = await upload.read(_MAX_AVATAR_BYTES + 1)
    if len(raw) > _MAX_AVATAR_BYTES:
        raise HTTPException(413, "Аватар больше 10 MiB")

    def prepare() -> tuple[bytes, str, str]:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > _MAX_AVATAR_PIXELS:
                    raise HTTPException(400, "Слишком большое разрешение аватара")
                source_format = (source.format or "").upper()
                if source_format == "GIF":
                    source.verify()
                    return raw, ".gif", "image/gif"
                source.load()
                image = source.convert("RGB")
                side = min(image.width, image.height)
                left = (image.width - side) // 2
                top = (image.height - side) // 2
                image = image.crop((left, top, left + side, top + side))
                image.thumbnail((_AVATAR_SIZE, _AVATAR_SIZE), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                image.save(out, format="WEBP", quality=90, method=6)
                return out.getvalue(), ".webp", "image/webp"
        except UnidentifiedImageError as exc:
            raise HTTPException(400, "Не удалось прочитать изображение аватара") from exc

    prepared, suffix, _media_type = await asyncio.to_thread(prepare)
    relative = Path("users") / str(user_id) / f"avatar{suffix}"
    target = Path(settings.site_asset_root) / relative

    def write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(prepared)
        temp.replace(target)
        for stale in target.parent.glob("avatar.*"):
            if stale == target or stale.name.endswith(".tmp"):
                continue
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

    await asyncio.to_thread(write)
    return relative.as_posix()


async def _expire_pending_registrations(db: AsyncSession, user_id: uuid.UUID) -> None:
    telegram_rows = (
        await db.execute(
            select(UserTelegramRegistration).where(
                UserTelegramRegistration.user_id == user_id,
                UserTelegramRegistration.confirmed_at_utc.is_(None),
            )
        )
    ).scalars().all()
    twitch_rows = (
        await db.execute(
            select(UserTwitchRegistration).where(
                UserTwitchRegistration.user_id == user_id,
                UserTwitchRegistration.confirmed_at_utc.is_(None),
            )
        )
    ).scalars().all()
    now = _utcnow()
    for row in [*telegram_rows, *twitch_rows]:
        if row.status != "expired":
            row.status = "expired"
            row.expires_at_utc = min(row.expires_at_utc, now)


@router.post("/register", status_code=201)
async def register(
    nickname: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    provider: str = Form(default="telegram"),
    avatar: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    nickname = _validate_nickname(nickname)
    login = _normalize_login(login)
    password = _validate_password(password, password_confirm)
    provider = provider.strip().lower()
    if provider not in {"telegram", "twitch"}:
        raise HTTPException(400, "Неизвестный способ подтверждения регистрации")
    if provider == "telegram":
        bot_username = _bot_username()
    else:
        _require_twitch_registration_config()
        bot_username = ""

    user = (
        await db.execute(select(User).where(func.lower(User.login) == login).limit(1))
    ).scalar_one_or_none()
    if user and _user_is_verified(user):
        raise HTTPException(409, "Этот логин уже занят")

    if user:
        user.nickname = nickname
        user.password_hash = await asyncio.to_thread(_password_hash_sync, password)
        if avatar is not None and avatar.filename:
            user.avatar_path = await _save_avatar(user.id, avatar)
        await _expire_pending_registrations(db, user.id)
    else:
        user = User(
            id=uuid.uuid4(),
            nickname=nickname,
            login=login,
            email=None,
            password_hash=await asyncio.to_thread(_password_hash_sync, password),
            is_active=True,
        )
        db.add(user)
        await db.flush()
        user.avatar_path = await _save_avatar(user.id, avatar)

    now = _utcnow()
    if provider == "telegram":
        start_token = secrets.token_urlsafe(24)
        poll_token = secrets.token_urlsafe(32)
        registration = UserTelegramRegistration(
            id=uuid.uuid4(),
            user_id=user.id,
            start_token_hash=_registration_token_hash("start", start_token),
            browser_token_hash=_registration_token_hash("browser", poll_token),
            status="pending",
            expires_at_utc=now + timedelta(seconds=settings.telegram_registration_ttl_seconds),
            created_at=now,
            updated_at=now,
        )
        db.add(registration)
        await db.commit()
        return {
            "ok": True,
            "provider": "telegram",
            "registration_id": str(registration.id),
            "poll_token": poll_token,
            "deep_link": f"https://t.me/{bot_username}?start={start_token}",
            "expires_in_seconds": settings.telegram_registration_ttl_seconds,
        }

    state_token = secrets.token_urlsafe(32)
    poll_token = secrets.token_urlsafe(32)
    expires_in = 15 * 60
    registration = UserTwitchRegistration(
        id=uuid.uuid4(),
        user_id=user.id,
        state_hash=_twitch_registration_token_hash("state", state_token),
        browser_token_hash=_twitch_registration_token_hash("browser", poll_token),
        status="pending",
        expires_at_utc=now + timedelta(seconds=expires_in),
        created_at=now,
        updated_at=now,
    )
    db.add(registration)
    await db.commit()

    redirect_uri = _twitch_registration_redirect_uri()
    authorize_url = f"{settings.twitch_authorize_url}?{urlencode({
        'client_id': settings.twitch_client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': settings.twitch_registration_scopes,
        'state': state_token,
        'force_verify': 'true',
    })}"
    return {
        "ok": True,
        "provider": "twitch",
        "registration_id": str(registration.id),
        "poll_token": poll_token,
        "authorize_url": authorize_url,
        "expires_in_seconds": expires_in,
    }


@router.get("/telegram-registration/status")
async def telegram_registration_status(
    response: Response,
    poll_token: str = Query(min_length=20, max_length=128),
    db: AsyncSession = Depends(get_db),
) -> dict:
    token_hash = _registration_token_hash("browser", poll_token)
    registration = (
        await db.execute(
            select(UserTelegramRegistration)
            .where(UserTelegramRegistration.browser_token_hash == token_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not registration:
        raise HTTPException(404, "Регистрация не найдена")

    now = _utcnow()
    if not registration.confirmed_at_utc and registration.expires_at_utc <= now:
        registration.status = "expired"
        await db.commit()
        return {"status": "expired"}

    if registration.confirmed_at_utc:
        user = await db.get(User, registration.user_id)
        if not user or not user.telegram_verified_at_utc or not user.is_active:
            raise HTTPException(409, "Регистрация подтверждена, но аккаунт недоступен")
        _set_auth_cookies(response, user)
        return {"status": "confirmed", "user": _user_payload(user)}

    return {
        "status": registration.status,
        "telegram_started": registration.started_at_utc is not None,
        "expires_at": registration.expires_at_utc.isoformat(),
    }


def _twitch_callback_page(message: str, ok: bool) -> HTMLResponse:
    color = "#65d58b" if ok else "#ef8b8b"
    body = f"""<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>MRW Hub</title>
<style>html,body{{height:100%;margin:0;background:#0d1217;color:#e8edf1;font:16px/1.45 system-ui,sans-serif}}body{{display:grid;place-items:center}}main{{max-width:520px;padding:30px;text-align:center}}strong{{color:{color}}}</style></head>
<body><main><strong>{message}</strong><p>Это окно можно закрыть.</p></main><script>setTimeout(()=>window.close(),900);</script></body></html>"""
    return HTMLResponse(body, status_code=200)


@router.get("/twitch-registration/status")
async def twitch_registration_status(
    response: Response,
    poll_token: str = Query(min_length=20, max_length=128),
    db: AsyncSession = Depends(get_db),
) -> dict:
    token_hash = _twitch_registration_token_hash("browser", poll_token)
    registration = (
        await db.execute(
            select(UserTwitchRegistration)
            .where(UserTwitchRegistration.browser_token_hash == token_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not registration:
        raise HTTPException(404, "Регистрация не найдена")

    now = _utcnow()
    if not registration.confirmed_at_utc and registration.expires_at_utc <= now:
        registration.status = "expired"
        await db.commit()
        return {"status": "expired"}

    if registration.confirmed_at_utc:
        user = await db.get(User, registration.user_id)
        if not user or not user.twitch_verified_at_utc or not user.is_active:
            raise HTTPException(409, "Регистрация подтверждена, но аккаунт недоступен")
        _set_auth_cookies(response, user)
        return {"status": "confirmed", "user": _user_payload(user)}

    return {"status": registration.status, "expires_at": registration.expires_at_utc.isoformat()}


@router.get("/twitch-registration/callback", response_class=HTMLResponse)
async def twitch_registration_callback(
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    _require_twitch_registration_config()
    state_hash = _twitch_registration_token_hash("state", state)
    registration = (
        await db.execute(
            select(UserTwitchRegistration)
            .where(UserTwitchRegistration.state_hash == state_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not registration:
        return _twitch_callback_page("Регистрация не найдена или ссылка устарела", False)

    now = _utcnow()
    if registration.confirmed_at_utc:
        return _twitch_callback_page("Регистрация уже подтверждена ✅", True)
    if registration.expires_at_utc <= now:
        registration.status = "expired"
        await db.commit()
        return _twitch_callback_page("Ссылка регистрации истекла", False)
    if error or not code:
        registration.status = "denied" if error == "access_denied" else "failed"
        await db.commit()
        return _twitch_callback_page("Подтверждение Twitch отменено", False)

    redirect_uri = _twitch_registration_redirect_uri()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_response = await client.post(
                settings.twitch_token_url,
                data={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            if token_response.status_code != 200:
                registration.status = "failed"
                await db.commit()
                return _twitch_callback_page("Twitch не подтвердил авторизацию", False)
            token_data = token_response.json()
            access_token = str(token_data.get("access_token") or "")
            if not access_token:
                registration.status = "failed"
                await db.commit()
                return _twitch_callback_page("Twitch не вернул токен авторизации", False)

            validation = await client.get(
                settings.twitch_validate_url,
                headers={"Authorization": f"OAuth {access_token}"},
            )
            if validation.status_code != 200:
                registration.status = "failed"
                await db.commit()
                return _twitch_callback_page("Не удалось проверить Twitch-аккаунт", False)
            profile = validation.json()
            twitch_user_id = str(profile.get("user_id") or "").strip()
            twitch_login = str(profile.get("login") or "").strip().lower()
            if not twitch_user_id or not twitch_login or profile.get("client_id") != settings.twitch_client_id:
                registration.status = "failed"
                await db.commit()
                return _twitch_callback_page("Twitch не вернул данные пользователя", False)

            # Registration needs identity only; do not store OAuth access/refresh tokens.
            try:
                await client.post(
                    settings.twitch_revoke_url,
                    data={"client_id": settings.twitch_client_id, "token": access_token},
                )
            except httpx.HTTPError:
                pass
    except httpx.HTTPError:
        registration.status = "failed"
        await db.commit()
        return _twitch_callback_page("Twitch временно недоступен. Повторите регистрацию", False)

    user = await db.get(User, registration.user_id)
    if not user:
        registration.status = "failed"
        await db.commit()
        return _twitch_callback_page("Пользователь регистрации не найден", False)

    linked_user = (
        await db.execute(
            select(User)
            .where(User.twitch_user_id == twitch_user_id, User.id != user.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if linked_user:
        registration.status = "failed"
        await db.commit()
        return _twitch_callback_page("Этот Twitch уже привязан к другому аккаунту MRW Hub", False)

    registration.status = "confirmed"
    registration.twitch_user_id = twitch_user_id
    registration.twitch_login = twitch_login
    registration.confirmed_at_utc = now
    user.twitch_user_id = twitch_user_id
    user.twitch_login = twitch_login
    user.twitch_verified_at_utc = now
    await db.commit()
    return _twitch_callback_page("Регистрация подтверждена ✅", True)


@router.post("/internal/telegram/start")
async def telegram_registration_start(
    payload: dict,
    x_internal_service_token: str | None = Header(default=None, alias="X-Internal-Service-Token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_internal_token(x_internal_service_token)
    start_token = str(payload.get("start_token") or "").strip()
    telegram_user_id = int(payload.get("telegram_user_id") or 0)
    telegram_chat_id = int(payload.get("telegram_chat_id") or 0)
    if not start_token or telegram_user_id <= 0 or telegram_chat_id == 0:
        raise HTTPException(400, "invalid telegram registration start payload")

    token_hash = _registration_token_hash("start", start_token)
    registration = (
        await db.execute(
            select(UserTelegramRegistration)
            .where(UserTelegramRegistration.start_token_hash == token_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not registration:
        raise HTTPException(404, "Регистрация не найдена или ссылка устарела")

    now = _utcnow()
    if not registration.confirmed_at_utc and registration.expires_at_utc <= now:
        registration.status = "expired"
        await db.commit()
        raise HTTPException(410, "Ссылка регистрации истекла. Вернитесь на сайт и повторите регистрацию")

    if registration.telegram_user_id and registration.telegram_user_id != telegram_user_id:
        raise HTTPException(409, "Эта ссылка регистрации уже открыта другим Telegram-пользователем")

    registration.telegram_user_id = telegram_user_id
    registration.telegram_chat_id = telegram_chat_id
    registration.telegram_username = (
        (str(payload.get("telegram_username") or "").strip() or None)[:64]
        if payload.get("telegram_username")
        else None
    )
    registration.telegram_first_name = (
        (str(payload.get("telegram_first_name") or "").strip() or None)[:255]
        if payload.get("telegram_first_name")
        else None
    )
    if not registration.started_at_utc:
        registration.started_at_utc = now
    if not registration.confirmed_at_utc:
        registration.status = "started"
    await db.commit()

    user = await db.get(User, registration.user_id)
    return {
        "ok": True,
        "registration_id": str(registration.id),
        "status": registration.status,
        "nickname": user.nickname if user else "MRW Hub",
        "confirmed": registration.confirmed_at_utc is not None,
    }


@router.post("/internal/telegram/confirm")
async def telegram_registration_confirm(
    payload: dict,
    x_internal_service_token: str | None = Header(default=None, alias="X-Internal-Service-Token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_internal_token(x_internal_service_token)
    try:
        registration_id = uuid.UUID(str(payload.get("registration_id") or ""))
    except ValueError as exc:
        raise HTTPException(400, "invalid registration id") from exc
    telegram_user_id = int(payload.get("telegram_user_id") or 0)
    if telegram_user_id <= 0:
        raise HTTPException(400, "invalid telegram user id")

    registration = await db.get(UserTelegramRegistration, registration_id)
    if not registration:
        raise HTTPException(404, "Регистрация не найдена")
    now = _utcnow()
    if not registration.confirmed_at_utc and registration.expires_at_utc <= now:
        registration.status = "expired"
        await db.commit()
        raise HTTPException(410, "Регистрация истекла")
    if registration.telegram_user_id != telegram_user_id:
        raise HTTPException(403, "Эта регистрация принадлежит другому Telegram-пользователю")

    user = await db.get(User, registration.user_id)
    if not user:
        raise HTTPException(404, "Пользователь регистрации не найден")

    linked_user = (
        await db.execute(
            select(User)
            .where(User.telegram_user_id == telegram_user_id, User.id != user.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if linked_user:
        raise HTTPException(409, "Этот Telegram уже привязан к другому аккаунту MRW Hub")

    if not registration.confirmed_at_utc:
        registration.status = "confirmed"
        registration.confirmed_at_utc = now
        user.telegram_user_id = telegram_user_id
        user.telegram_username = registration.telegram_username
        user.telegram_verified_at_utc = now
        await db.commit()

    return {"ok": True, "status": "confirmed", "nickname": user.nickname}


@router.post("/login")
async def login(payload: dict, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    login_value = _normalize_login(str(payload.get("login") or ""))
    password = str(payload.get("password") or "")
    user = (await db.execute(select(User).where(func.lower(User.login) == login_value).limit(1))).scalar_one_or_none()
    valid = bool(user and await asyncio.to_thread(_password_verify_sync, password, user.password_hash))
    if not valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт отключён")
    if not _user_is_verified(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Регистрация ещё не подтверждена через Telegram или Twitch")
    _set_auth_cookies(response, user)
    return {"ok": True, "user": _user_payload(user)}


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user = await require_current_user(request, db)
    return {"user": _user_payload(user)}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = _decode_token(request.cookies.get(settings.auth_refresh_cookie_name), "refresh")
    user = await _load_user(db, user_id)
    _set_auth_cookies(response, user)
    return {"ok": True, "user": _user_payload(user)}


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    _clear_auth_cookies(response)
    response.status_code = 204
    return response


@router.get("/users/{user_id}/avatar")
async def user_avatar(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> FileResponse:
    user = await db.get(User, user_id)
    if not user or not user.avatar_path:
        raise HTTPException(404, "Аватар не найден")
    root = Path(settings.site_asset_root).resolve()
    path = (root / user.avatar_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Аватар не найден")
    media_type = "image/gif" if path.suffix.lower() == ".gif" else "image/webp"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
