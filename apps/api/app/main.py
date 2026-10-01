from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from streamhub_common.logging import configure_logging
from streamhub_common.settings import get_settings

from .routers import auth, capture, events, sessions, storage, video

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="StreamHub API", version="0.1.0", debug=settings.app_debug)
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
app.include_router(storage.router)
app.include_router(auth.router)
