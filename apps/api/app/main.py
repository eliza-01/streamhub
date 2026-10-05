from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from streamhub_common.logging import configure_logging
from streamhub_common.settings import get_settings

from .routers import auth, capture, events, playback, sessions, site, storage, telegram, user_auth, video
from .telegram.runtime import telegram_runtime

settings = get_settings()
configure_logging(settings.log_level)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await telegram_runtime.start()
    await events.event_purge_queue_runtime.start()
    try:
        yield
    finally:
        await events.event_purge_queue_runtime.stop()
        await telegram_runtime.stop()


app = FastAPI(title="StreamHub API", version="0.1.0", debug=settings.app_debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


app.include_router(sessions.router)
app.include_router(events.router)
app.include_router(capture.router)
app.include_router(video.router)
app.include_router(telegram.router)
app.include_router(playback.router)
app.include_router(site.router)
app.include_router(storage.router)
app.include_router(auth.router)
app.include_router(user_auth.router)
