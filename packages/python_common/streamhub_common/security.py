from __future__ import annotations

import hmac

from cryptography.fernet import Fernet
from fastapi import Header, HTTPException, status

from .settings import get_settings


def require_internal_token(x_internal_service_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_service_token
    if not x_internal_service_token or not hmac.compare_digest(x_internal_service_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token")


def encrypt_secret(value: str) -> str:
    f = Fernet(get_settings().oauth_token_encryption_key.encode("ascii"))
    return f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    f = Fernet(get_settings().oauth_token_encryption_key.encode("ascii"))
    return f.decrypt(value.encode("ascii")).decode("utf-8")
