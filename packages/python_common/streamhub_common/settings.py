from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    project_name: str = Field(default="streamhub", alias="PROJECT_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    public_api_base_url: str = Field(default="http://localhost:18741", alias="PUBLIC_API_BASE_URL")
    web_origin: str = Field(default="http://localhost:18742", alias="WEB_ORIGIN")
    cors_allow_origin_regex: str = Field(default=r"^http://localhost:18742$", alias="CORS_ALLOW_ORIGIN_REGEX")

    database_url: str = Field(alias="DATABASE_URL")
    internal_service_token: str = Field(alias="INTERNAL_SERVICE_TOKEN")
    oauth_token_encryption_key: str = Field(alias="OAUTH_TOKEN_ENCRYPTION_KEY")

    chat_ingest_base_url: str = Field(default="http://chat-ingest:8001", alias="CHAT_INGEST_BASE_URL")
    twitch_adapter_base_url: str = Field(default="http://twitch-adapter:8002", alias="TWITCH_ADAPTER_BASE_URL")
    video_recorder_base_url: str = Field(default="http://video-recorder:8003", alias="VIDEO_RECORDER_BASE_URL")

    video_output_root_1: str = Field(default="/outputs/root1", alias="VIDEO_OUTPUT_ROOT_1")
    video_output_root_1_label: str = Field(default="root1", alias="VIDEO_OUTPUT_ROOT_1_LABEL")
    video_output_root_2: str = Field(default="/outputs/root2", alias="VIDEO_OUTPUT_ROOT_2")
    video_output_root_2_label: str = Field(default="root2", alias="VIDEO_OUTPUT_ROOT_2_LABEL")
    video_output_root_2_enabled: bool = Field(default=False, alias="VIDEO_OUTPUT_ROOT_2_ENABLED")
    video_output_root_3: str = Field(default="/outputs/root3", alias="VIDEO_OUTPUT_ROOT_3")
    video_output_root_3_label: str = Field(default="root3", alias="VIDEO_OUTPUT_ROOT_3_LABEL")
    video_output_root_3_enabled: bool = Field(default=False, alias="VIDEO_OUTPUT_ROOT_3_ENABLED")
    video_spool_root: str = Field(default="/spool", alias="VIDEO_SPOOL_ROOT")
    video_segment_seconds: int = Field(default=10, ge=4, alias="VIDEO_SEGMENT_SECONDS")
    video_storage_copy_workers: int = Field(default=1, ge=1, le=4, alias="VIDEO_STORAGE_COPY_WORKERS")
    video_archive_retry_seconds: float = Field(default=5.0, ge=1.0, alias="VIDEO_ARCHIVE_RETRY_SECONDS")
    video_archive_batch_segments: int = Field(default=100, ge=1, le=1000, alias="VIDEO_ARCHIVE_BATCH_SEGMENTS")
    video_spool_min_free_bytes: int = Field(default=536870912, ge=0, alias="VIDEO_SPOOL_MIN_FREE_BYTES")
    video_archive_min_free_bytes: int = Field(default=1073741824, ge=0, alias="VIDEO_ARCHIVE_MIN_FREE_BYTES")
    video_stream_quality: str = Field(default="best", alias="VIDEO_STREAM_QUALITY")
    video_reconnect_seconds: float = Field(default=5.0, ge=1.0, alias="VIDEO_RECONNECT_SECONDS")
    video_stream_timeout_seconds: int = Field(default=90, ge=15, alias="VIDEO_STREAM_TIMEOUT_SECONDS")
    video_stop_timeout_seconds: int = Field(default=20, ge=5, alias="VIDEO_STOP_TIMEOUT_SECONDS")

    twitch_client_id: str = Field(alias="TWITCH_CLIENT_ID")
    twitch_client_secret: str | None = Field(default=None, alias="TWITCH_CLIENT_SECRET")
    twitch_legacy_oauth_token: str | None = Field(default=None, alias="TWITCH_LEGACY_OAUTH_TOKEN")
    twitch_use_legacy_token_if_valid: bool = Field(default=True, alias="TWITCH_USE_LEGACY_TOKEN_IF_VALID")
    twitch_device_scopes: str = Field(default="user:read:chat", alias="TWITCH_DEVICE_SCOPES")
    twitch_irc_fallback_scopes: str = Field(default="chat:read", alias="TWITCH_IRC_FALLBACK_SCOPES")
    twitch_validate_url: str = Field(default="https://id.twitch.tv/oauth2/validate", alias="TWITCH_VALIDATE_URL")
    twitch_device_url: str = Field(default="https://id.twitch.tv/oauth2/device", alias="TWITCH_DEVICE_URL")
    twitch_token_url: str = Field(default="https://id.twitch.tv/oauth2/token", alias="TWITCH_TOKEN_URL")
    twitch_api_base_url: str = Field(default="https://api.twitch.tv/helix", alias="TWITCH_API_BASE_URL")
    twitch_eventsub_ws_url: str = Field(default="wss://eventsub.wss.twitch.tv/ws", alias="TWITCH_EVENTSUB_WS_URL")
    twitch_irc_host: str = Field(default="irc.chat.twitch.tv", alias="TWITCH_IRC_HOST")
    twitch_irc_port: int = Field(default=6697, alias="TWITCH_IRC_PORT")
    twitch_irc_use_tls: bool = Field(default=True, alias="TWITCH_IRC_USE_TLS")

    provider_outbox_batch_max: int = Field(default=500, alias="PROVIDER_OUTBOX_BATCH_MAX")
    provider_outbox_flush_ms: int = Field(default=500, alias="PROVIDER_OUTBOX_FLUSH_MS")
    provider_outbox_max_messages: int = Field(default=250000, alias="PROVIDER_OUTBOX_MAX_MESSAGES")
    ingest_batch_max: int = Field(default=500, alias="INGEST_BATCH_MAX")
    ingest_max_body_bytes: int = Field(default=1048576, alias="INGEST_MAX_BODY_BYTES")
    playback_page_size: int = Field(default=500, alias="PLAYBACK_PAGE_SIZE")
    soft_delete_retention_days: int = Field(default=30, alias="SOFT_DELETE_RETENTION_DAYS")

    capture_require_complete: bool = Field(default=True, alias="CAPTURE_REQUIRE_COMPLETE")
    live_eventsub_enabled: bool = Field(default=True, alias="LIVE_EVENTSUB_ENABLED")
    live_irc_redundancy_enabled: bool = Field(default=True, alias="LIVE_IRC_REDUNDANCY_ENABLED")
    live_post_stream_vod_reconciliation: bool = Field(default=True, alias="LIVE_POST_STREAM_VOD_RECONCILIATION")
    live_allow_complete_with_known_gaps: bool = Field(default=False, alias="LIVE_ALLOW_COMPLETE_WITH_KNOWN_GAPS")

    vod_full_chat_enabled: bool = Field(default=True, alias="VOD_FULL_CHAT_ENABLED")
    vod_replay_gql_url: str = Field(default="https://gql.twitch.tv/gql", alias="VOD_REPLAY_GQL_URL")
    vod_replay_operation_name: str = Field(default="VideoCommentsByOffsetOrCursor", alias="VOD_REPLAY_OPERATION_NAME")
    vod_replay_persisted_query_sha256: str = Field(alias="VOD_REPLAY_PERSISTED_QUERY_SHA256")
    vod_replay_client_id: str = Field(alias="VOD_REPLAY_CLIENT_ID")
    vod_replay_start_offset_seconds: int = Field(default=0, alias="VOD_REPLAY_START_OFFSET_SECONDS")
    vod_replay_page_concurrency: int = Field(default=1, alias="VOD_REPLAY_PAGE_CONCURRENCY")
    vod_replay_request_timeout_seconds: int = Field(default=20, alias="VOD_REPLAY_REQUEST_TIMEOUT_SECONDS")
    vod_replay_retry_max: int = Field(default=20, alias="VOD_REPLAY_RETRY_MAX")
    vod_replay_retry_base_seconds: float = Field(default=2.0, alias="VOD_REPLAY_RETRY_BASE_SECONDS")
    vod_replay_retry_max_seconds: float = Field(default=120.0, alias="VOD_REPLAY_RETRY_MAX_SECONDS")
    vod_replay_checkpoint_every_pages: int = Field(default=1, alias="VOD_REPLAY_CHECKPOINT_EVERY_PAGES")
    vod_replay_require_end_of_pagination: bool = Field(default=True, alias="VOD_REPLAY_REQUIRE_END_OF_PAGINATION")

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("mysql+asyncmy://", "mysql+pymysql://", 1)

    @property
    def twitch_requested_scopes(self) -> str:
        scopes = self.twitch_device_scopes.split()
        if self.live_irc_redundancy_enabled:
            scopes.extend(self.twitch_irc_fallback_scopes.split())
        return " ".join(dict.fromkeys(scope for scope in scopes if scope))

    @property
    def normalized_legacy_token(self) -> str | None:
        if not self.twitch_legacy_oauth_token:
            return None
        token = self.twitch_legacy_oauth_token.strip()
        return token[6:] if token.lower().startswith("oauth:") else token


@lru_cache
def get_settings() -> Settings:
    return Settings()
