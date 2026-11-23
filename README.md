# Afterplay Creator Studio

A working, single-user local video creation platform. React + TypeScript + Vite, FastAPI, SQLAlchemy, FFmpeg, local Faster Whisper, optional OpenAI transcript analysis and AudD music recognition. The existing gta6-clipper installation and its outputs are preserved.

## Run

From this directory:

```sh
./launch.sh
```

Open http://localhost:8787. Or double-click `Open Creator Platform.command` in the parent Shorts folder. The server binds to loopback only.

First installation on another machine:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
npm --prefix frontend ci
npm --prefix frontend run build
./launch.sh
```

Install FFmpeg with libass (`ass` filter) and libx264. On this Mac the full build is at `/opt/homebrew/opt/ffmpeg-full/bin`. Override `FFMPEG_PATH` and `FFPROBE_PATH` elsewhere. For development, run the API and `npm --prefix frontend run dev`; Vite proxies `/api` to port 8787. API docs: http://localhost:8787/docs.

## Architecture

```text
frontend/src/main.tsx        App shell, dashboard, live activity and project library
frontend/src/dialogs.tsx     Upload, processing setup and video editor
frontend/src/pages.tsx       Analytics, publishing, safety settings and policy configuration
frontend/src/shared.tsx      Reusable modal and empty-state components
frontend/src/types.ts       REST client and shared frontend types
frontend/src/style.css      Responsive dark gaming design
backend/main.py             Validated API, streaming uploads, asset delivery and local-origin checks
backend/db.py               SQLAlchemy models, SQLite / PostgreSQL connection
backend/worker.py           Serialized media worker, persistent jobs, progress, interruption recovery
backend/media.py            Probe, chronological splitting, SRT import, ASS karaoke, FFmpeg export
backend/ai.py               Responses API highlights/post copy + explicitly labeled offline fallback
backend/social.py           Official OAuth, encrypted credentials, refresh, analytics adapters
backend/publishing.py       Durable publishing scheduler, snapshots, dedupe, retries and history
backend/publish_providers.py YouTube resumable upload and TikTok Direct Post wire protocols
backend/publishing_api.py   Account setup, upload consent, queue control and fallback API
frontend/src/accounts.tsx   Local encrypted developer-app setup and official account connection
frontend/src/publishing-page.tsx Dual review, scheduling, independent statuses and manual handoff
backend/safety.py           Optional sampled AudD recognition (not copyright clearance)
data/                      Local database, sources, speech models, rendered assets (gitignored)
tests/test_workflow.py      Real upload → caption → split → render → trim → render → download test
```

### Workflow

1. Create a project by browsing or dropping MP4/MOV/MKV/WebM/AVI/M4V. Confirm permission to edit. Uploads stream to disk (default limit 20 GB), are probed and hashed for duplicate warnings. Source limit: six hours.
2. Choose a category, chronological full-video parts, offline audio peaks, or AI story selection. Set 15–60s or 60–90s bounds. Chronological mode covers the whole source with evenly sized parts, without losing a trailing remainder. If mathematically impossible (e.g. a 91s source divided into 60–90s parts), the job fails with an actionable explanation.
3. Use local transcription (first use downloads the Whisper base model), or attach SRT. AI selection requires a transcript. Chronological processing works with no AI credentials. Offline audio peaks are a heuristic, never labeled as AI.
4. Prepare editable drafts or render immediately. Exports preserve the full picture over a blurred vertical background by default; optional center crop. All exports are 1080×1920, H.264/AAC MP4, 30fps with karaoke subtitles and Part labels. Thumbnails, SRT and metadata JSON are generated separately.
5. Open Clip studio to edit source in/out points, caption words, titles, hooks, TikTok caption, YouTube description, hashtags, keywords, thumbnail concept and pinned comment. Saving invalidates the prior export and queues a new render. Export each asset separately.
6. Inspect content safety and project music-review results. Prepare publishing drafts. Use Publishing to review and explicitly authorize independent platform jobs. No content is posted without a user-created authorized queue item.

Progress and jobs are stored in the database. One worker limits memory pressure; same-project concurrent jobs are rejected. Server restarts mark unfinished jobs as failed with a Retry action. Retrying a partially rendered batch reuses its prepared clips. This is a local persistent job registry, not a distributed task broker.

## Optional credentials

Copy `.env.example` to `.env` and supply your own values. `launch.sh` loads it; do not commit it. With uvicorn launched directly, export these variables yourself.

- `OPENAI_API_KEY` + `OPENAI_MODEL`: enable Responses API story selection and per-clip copy generation. The model must be available to your account; no model is silently selected. Only the transcript excerpt and category are sent, and only when AI is selected. Generated windows are bounds-checked, duration-checked and non-overlapping. Prompt content is treated as untrusted source text. Large transcripts fail clearly rather than silently truncate.
- `AUDD_API_TOKEN`: enables an explicit per-job music-check option. Up to five 12-second audio samples are sent to AudD. Recognized tracks are flagged as potential copyrighted music. No match is not rights clearance; it is not an exhaustive Content ID scan.
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`: official Google OAuth credentials. Enable YouTube Data API and YouTube Analytics API. Register `http://localhost:8787/api/accounts/youtube/callback`.
- `TIKTOK_CLIENT_ID`, `TIKTOK_CLIENT_SECRET`: official TikTok app credentials (client key goes in CLIENT_ID). Register `http://localhost:8787/api/accounts/tiktok/callback`, subject to TikTok redirect URI rules, products, scopes and app approval. If localhost is not accepted by your approved app, deploy an authenticated server with an approved callback; do not bypass restrictions.
- `TOKEN_ENCRYPTION_KEY`: optional override for the automatically generated owner-only local key in `data/oauth.key`. Keep the key stable and secret. OAuth tokens are encrypted in the database, never returned to the frontend. Disconnect removes local tokens; revoke provider access separately if desired.
- `DATABASE_URL=postgresql+psycopg://user:password@localhost/afterplay`: PostgreSQL support. SQLite is the local default. Start a provisioned empty database before first launch. Schema is initialized with SQLAlchemy; production migrations are not included.

The workspace includes a clearly labeled synthetic browser-test project and its playable export, demonstrating the completed UI workflow. No credentialed provider requests were made during development. AI, music recognition, live OAuth and official analytics require an account and credentials for end-to-end verification.

## Analytics and monetization

YouTube adapter requests channel statistics and 28-day Shorts reports (views, watch minutes, weighted average percentage viewed, engagement), with a separate official estimated-revenue query. Unsupported monetary access produces no revenue estimate. TikTok Display API returns follower statistics and latest-20-video lifetime counters with engagement; this is explicitly not a 30-day view total. Retention, watch time and Creator Rewards revenue remain unavailable in this adapter. Comparison tables show returned per-day/per-video performance.

YouTube and TikTok requirements are stored separately and editable in Settings. Subscriber/follower progress uses official account data when available. Qualified-view eligibility progress stays unavailable rather than using the wrong view count/window. Numeric thresholds alone do not establish eligibility. Region, age, account status, originality, review and changing official policies apply. Review configured values against the platform's current rules.

## Dual-platform publishing

See [ACCOUNT_SETUP.md](ACCOUNT_SETUP.md) for your registered Google/TikTok app setup and official OAuth connection. Local credential forms encrypt client secrets; no passwords are stored. A persistent local encryption key is automatically created if an environment key is not supplied.

The publishing implementation is operational code, not a placeholder:

- YouTube official resumable upload sessions, chunk transfers, persisted offsets, safe recovery, private/unlisted/public visibility, metadata, video ID and status polling. Native scheduled releases use a private upload with `status.publishAt`. A processed private/unlisted video is **available**, not reported as publicly published. Public success is only recorded after the platform confirms processing/visibility.
- TikTok official creator-info preflight, `video.publish` Direct Post initialization, sequential chunk transfers, upload offsets and publish-status polling. Publication is only confirmed by `PUBLISH_COMPLETE`. Public post IDs/links are saved only when returned; a private confirmed post can have no public URL.
- TikTok Direct Post is disabled by default for this private local utility, in accordance with TikTok’s current intended-use guidance. An approved app with a permitted audience/use may enable the adapter. Unaudited clients remain private-only. No flag bypasses provider review or limits.
- The official manual TikTok fallback provides the exact frozen MP4, editable prepared caption and TikTok Studio upload URL. It stays **manual required**; the app never fabricates manual-publication success.
- Dual upload creates independent records and statuses per platform. Exact file hash + authorized account ID + platform prevents duplicate submissions, including duplicate clips. The approved export is copied and hashed in one pass to an immutable snapshot, so later edits do not change queued content.
- The persistent five-second scheduler enforces configurable daily upload-start limits, per-platform concurrency, exponential retries of temporary errors, user pause/resume/cancel controls, and event history. UTC is used for daily limit reset; schedule inputs are converted from the browser’s local timezone to an absolute UTC instant.
- A native YouTube schedule can be paused/cancelled by retaining the uploaded video privately. Global queue pause only stops unsent uploads; it does not undo native schedules or already published posts. In-flight provider requests cannot be cancelled locally. Resume keeps the existing provider ID rather than creating a duplicate.
- TikTok delayed sends are enabled only via an explicit server setting for an approved app/use where permitted. Otherwise a requested TikTok schedule uses the manual workflow.
- Upload sessions (which can contain signed URL credentials) are encrypted and omitted from API responses and event messages. If an initialization request’s response is lost, the job becomes **needs review** rather than risking a duplicate. Known sessions resume or reconcile. Explicit failed-HTTP rejection can be retried with bounded exponential backoff.
- Restart recovery resumes persisted sessions and flags uncertain operations for review. The scheduler runs while this computer/server is running; it is not a hosted always-on service.

New API endpoints are under `/api/publishing/`: config, queue, creator preflight, history, events, job controls and manual download handoff. Legacy draft publishing cannot bypass review; it directs users to the current explicit publish flow.

Accounts are not connected until the owner supplies their developer-app credentials locally and completes official OAuth. Live uploads and provider-account consent are not part of automated tests.

## Validation

```sh
.venv/bin/python -m pytest tests -q
npm --prefix frontend run build
```

Tests create temporary synthetic video and a temporary database, run real FFmpeg 1080×1920 renders, exercise SRT import, trim/save/re-render, downloads, post-draft preparation, missing credentials, OAuth state rejection, origin/host protection and configurable policies. Publishing tests also exercise the actual HTTP protocol code with mock official responses: YouTube resumability, TikTok chunk boundaries, independent confirmed statuses, duplicate snapshots, retries, restart recovery, privacy restrictions, daily limits, account binding and encrypted refresh-token rotation. No real social post or paid AI call occurs. Local speech-model downloads and live provider credentials are intentionally outside this deterministic test.

## Production boundary

This is an operational local MVP, not a hosted multi-user SaaS. It has no user login, tenant isolation, distributed job queue, object storage, billing or autoscaling. Do not bind it publicly. Before an internet deployment, add authentication and authorization, user-scoped media, migrations, a durable external job broker/worker, storage quotas, upload scanning, encrypted secrets management, rate limits, TLS and operational monitoring. FFmpeg jobs do not run in a Cloudflare Worker, so a static Sites deployment would not provide the processing backend.

## Official integration references

- https://developers.openai.com/api/reference/resources/responses/methods/create
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos#status.publishAt
- https://developers.google.com/youtube/analytics/reference/reports/query
- https://developers.tiktok.com/doc/content-posting-api-get-started
- https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- https://developers.tiktok.com/doc/tiktok-api-v2-video-query
- https://docs.audd.io/
- https://support.google.com/youtube/answer/72851
- https://support.tiktok.com/en/business-and-creator/creator-rewards-program/creator-rewards-program
