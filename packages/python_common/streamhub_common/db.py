from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import BINARY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .settings import get_settings


class Base(DeclarativeBase):
    pass


class UUIDBinary(TypeDecorator[uuid.UUID]):
    impl = BINARY(16)
    cache_ok = True

    def process_bind_param(self, value: uuid.UUID | str | None, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value.bytes

    def process_result_value(self, value: bytes | None, dialect):
        if value is None:
            return None
        return uuid.UUID(bytes=value)


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
