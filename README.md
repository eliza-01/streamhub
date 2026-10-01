# StreamHub

Initial implementation of the StreamHub MVP from technical specification v1.2.

## What is implemented in this increment

- Monorepo skeleton and Docker Compose with MySQL 8.4, phpMyAdmin, API gateway, separate `chat-ingest`, separate `twitch-adapter`, and React/Vite web UI.
- Alembic migration with `sessions`, `session_segments`, `chat_messages`, `chat_events`, OAuth token storage, capture jobs and audit log.
- Public API under `/api/v1/...`, internal services protected with `X-Internal-Service-Token` and not published outside the Docker network.
- Idempotent chat message ingest with provider-id/fallback SHA-256 deduplication and a monotonically assigned per-session `sequence_no`.
- VOD full-chat worker in `twitch-adapter`: starts from offset 0, follows Twitch replay-chat cursor pagination, sends normalized batches to `chat-ingest`, stores a durable MySQL checkpoint, retries with exponential backoff, resumes after adapter restart, and marks the session complete only after end-of-pagination is confirmed.
- VOD Pause/Resume/Stop semantics based on the durable checkpoint.
- Twitch Device Code Flow proxy through the backend; runtime access/refresh tokens are encrypted before MySQL storage. Legacy token validation is attempted on adapter startup but failure does not stop the service.
- MV3 Chrome extension skeleton: Twitch route/player detection, LIVE/VOD context, Start/Pause/Resume/Stop controls, confirmation on Stop, Device Code authorization button and persisted local control state.
- Web UI: session list and basic chat playback with Play/Pause/Stop, slider seek and HH:MM:SS seek.
- `.env` is gitignored. `.env.example` contains no supplied secrets.

## Explicitly not claimed complete yet

The specification requires LIVE capture to use EventSub and a redundant IRC reader, followed by VOD reconciliation when available. That part is not implemented in this increment; the internal LIVE start endpoint returns an explicit `501` instead of pretending that lossless LIVE capture exists. The next implementation step is EventSub + IRC redundancy, source health and post-live reconciliation.

The Twitch VOD replay GraphQL interface is intentionally isolated in `apps/twitch_adapter`. It is not a public Helix API and may change. If the configured persisted query stops working, the worker records an error/retries and ultimately marks the session `FAILED`; it never silently treats browser DOM data as complete.

## Run locally

The supplied local environment file has been copied to `.env` in the working project and is excluded by `.gitignore`.

```bash
cd streamhub
docker compose up --build
```

On the first MySQL 8.4 startup (especially Docker Desktop on Windows), initializing a brand-new volume can take several minutes. The Compose healthcheck includes a 5-minute `start_period` so dependent services do not fail while MySQL is still creating its data files.

If the previous first-start attempt created a partial development volume and there is no unarchived video in `streamhub_video_spool`, reset it once before retrying:

```bash
docker compose down -v --remove-orphans
docker compose up --build
```

Then open:

- Web UI: `http://localhost:18742`
- API docs: `http://localhost:18741/docs`
- phpMyAdmin: `http://localhost:18743`

The internal `chat-ingest:8001` and `twitch-adapter:8002` services are reachable only inside the Compose network.

### Load the Chrome extension

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose **Load unpacked**.
4. Select `apps/chrome_extension`.
5. Open a Twitch VOD page with the actual player loaded and use **Start recording**.

For VOD, the extension passes only context and the selected `video_id`; after Start the backend worker owns the collection and continues independently of replay-chat visibility, seeking, tab visibility or the Twitch tab itself.

## Useful commands

```bash
make up
make logs
make down
make test
make lint
```

To reset the development database and named volumes completely **only when no unarchived video remains in spool**:

```bash
docker compose down -v
```

## Repository layout

```text
apps/
  api/               public API/session service
  chat_ingest/       isolated normalization/dedup/storage service
  twitch_adapter/    Twitch provider adapter and VOD worker
  video_recorder/     Streamlink/FFmpeg capture + spool/archive handoff
  web/               React/Vite UI
  chrome_extension/  Manifest V3 control client
packages/
  python_common/     shared settings, models, contracts, security
migrations/          Alembic migrations
tests/               initial invariant/contract tests
docker-compose.yml
.env                  local secrets, gitignored
.env.example          safe template
```

## v1.3 video storage handoff

`video-recorder` writes closed MPEG-TS segments to the Docker named volume `streamhub_video_spool` first. A backend storage worker performs a two-phase handoff to the selected physical output root mounted under `/outputs/*`:

1. copy the closed spool segment to a unique `.partial-*` file;
2. flush/fsync and verify size + SHA-256 read-back;
3. atomically publish the final `seg_XXXXXX.ts` with `os.replace`;
4. commit `video_segments.storage_state=archive_ready` and the archive-relative path;
5. only after that DB commit, delete the spool copy.

The recorder uses a bounded Docker spool plus explicit physical output roots. `VIDEO_OUTPUT_ROOT_1_HOST` and `VIDEO_OUTPUT_ROOT_2_HOST` are absolute host paths mounted as `/outputs/root1` and `/outputs/root2` (Windows examples: `E:/StreamHub/output` and `F:/StreamHub/output`). There is no project-relative fallback. Web → **Хранилище** selects the enabled root and directory inside it. A Video session snapshots that choice at Start, so changing the default never moves an active session mid-recording.

The UI shows the configured root labels. Set `VIDEO_OUTPUT_ROOT_2_ENABLED=true` when the second mounted root should be selectable. The Storage page can then create a durable background migration from one enabled output root to the other; only idle sessions whose segments are already `archive_ready` are moved, each session tree is verified on the destination before the DB root switch, and the old tree is removed only after that commit.

Closed segments are released from spool in verified batches (`VIDEO_ARCHIVE_BATCH_SEGMENTS`, default `100`). While recording, a batch is copied to the selected physical output, SHA-256 read-back is verified, the batch is committed as `archive_ready`, and only then are those spool copies removed. Stop/EOF flushes the final remainder smaller than the batch size.
```text
<selected output root>/<web subdir>/
  twitch/events/<event_id>/video/<video_session_id>/
    session.json
    recording.m3u8
    runs/run_000001.m3u8
    segments/seg_000001.ts
    parts/
    logs/recorder.log
```

The storage worker is restart-idempotent at the important crash points: interrupted `copying` rows return to `spool`; an already published final segment can be verified and committed after restart; and an `archive_ready` row with a leftover spool copy is cleaned up only after the archive file is verified.

**Do not use `docker compose down -v` while uncommitted video exists in spool.** `-v` deletes the named spool volume. Physical output roots are bind mounts and are separate from Compose named volumes.
