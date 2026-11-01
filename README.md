# ClipFoam

**Turn long videos into short-form gold.** ClipFoam is an open-source, self-hostable alternative to
AI clipping suites: upload a long video, get a real transcript, find ranked highlight candidates,
cut and caption clips in a browser editor, render MP4s with FFmpeg, and schedule posts — all without
sending your media to a third party or paying for an AI key.

> **Honesty is a feature.** ClipFoam never fabricates a transcript, a render, an "AI" response or a
> publish event. When a provider is missing it fails loudly with a typed error code, labels
> deterministic output as deterministic, and tells you what to configure instead.

---

## What works today

Verified end to end in this repository (`75` backend tests, production frontend build):

| Area | Status |
| --- | --- |
| Email/password auth, HttpOnly session cookies, CSRF double-submit, roles (owner/admin/editor/viewer) | ✅ implemented |
| Multi-tenant projects/media/transcripts/clips/exports with server-side isolation | ✅ implemented |
| Upload validation (extension, MIME, container signature, size, duration) | ✅ implemented |
| URL import with SSRF guards (scheme, credentials, private/metadata IPs, redirect re-validation) | ✅ implemented |
| Transcript import (SRT / VTT / JSON) with editable, timestamped segments | ✅ implemented |
| Local transcription with faster-whisper | ⚠️ code path implemented; requires `requirements-local-ai.txt` + model download |
| Deterministic highlight detection + ranking (hooks, salience, boundaries, length fit, density, cohesion) | ✅ implemented |
| Optional AI ranking/copy through a provider registry (Ollama, OpenAI-compatible) | ⚠️ implemented; off by default (`CF_AI_PROVIDER=none`) |
| Clip editor: trim, aspect ratio, caption presets/karaoke, colours, safe areas, logo overlay | ✅ implemented |
| Render pipeline: FFmpeg trim → reframe → captions (libass) → logo → loudnorm → H.264/AAC MP4 | ✅ implemented |
| Durable jobs: QUEUED…COMPLETED/FAILED/CANCELLED, retries with backoff, cancellation, SSE progress | ✅ implemented |
| Content studio: titles/hooks/descriptions/hashtags/captions, editable + versioned | ✅ implemented (deterministic by default) |
| Brand kits (colours, fonts, caption preset, logo) usable in renders | ✅ implemented |
| Signed, expiring file URLs (local disk or S3/R2) | ✅ implemented |
| Social publishing | ⚠️ **YouTube and Mastodon only**; TikTok/Instagram/X/LinkedIn are manual-export with an explicit reason in the UI |
| Engagement analytics | ⚠️ real API metrics only for connected platforms that granted read scope; otherwise "unavailable" |
| Background music | ❌ no music library ships; selecting a track fails the render with `music_unavailable` instead of silently ignoring it |
| Docker / Compose / CI / deep docs | ❌ not in this change set (see Roadmap) |

Everything the UI shows — counts, storage, scores, job stages, export sizes — is read from the
database. There are no hard-coded statistics and no mock data paths.

---

## Architecture

```
apps/web  (Next.js 15 App Router, TypeScript strict, Tailwind, shadcn-style UI, Zustand)
   │  /api/v1/* proxied server-side (API_PROXY_TARGET) — no secrets in the browser
   ▼
apps/api  (FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic)
   ├── app/services   media probing, transcription, highlights, captions (ASS/SRT/VTT),
   │                  render planning, import/SSRF, publishing, content, audit, validation
   ├── app/workers    durable queue, handlers, worker loop (claim → lease → retry)
   ├── app/api/routes auth, projects, media, transcripts, candidates, clips, content,
   │                  brand-kits, social, jobs (SSE), analytics, system, files
   └── storage        local disk (dev) or S3/R2 (signed URLs, private objects)

PostgreSQL (prod) / SQLite (dev)   ·   Redis optional   ·   FFmpeg + libass   ·   PyAV ffprobe fallback
```

Job lifecycle is persisted per row: `QUEUED → VALIDATING → INGESTING → TRANSCRIBING → ANALYZING →
GENERATING_PREVIEWS → RENDERING → STORING_OUTPUTS → COMPLETED | FAILED | CANCELLED`.

---

## Quick start (no paid API keys)

**Prerequisites:** Python 3.11+, Node 20+, and FFmpeg/FFprobe on `PATH` (or use the bundled
`imageio-ffmpeg` binary — ClipFoam falls back automatically and says so in `/api/v1/health`).

```bash
# 1. Configuration
cp .env.example .env
python -c "import secrets; print('CF_SECRET_KEY=' + secrets.token_urlsafe(48))"   # paste into .env

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/api/requirements-dev.txt
cd apps/api
alembic upgrade head
uvicorn app.main:app --reload --port 8000            # API + docs at /api/docs

# 3. Worker (second terminal) — or set CF_ENABLE_INLINE_WORKER=true for a single-process demo
cd apps/api && python -m app.workers.worker

# 4. Frontend (third terminal)
cd apps/web && npm install && npm run dev            # http://localhost:3000
```

Open <http://localhost:3000>, create an account, and you are in the pipeline.

### Optional: local transcription

```bash
pip install -r apps/api/requirements-local-ai.txt    # faster-whisper
# first run downloads the model (CF_WHISPER_MODEL=small by default)
```

Without it, transcription jobs fail with an explicit message and the editor offers **Import cues**
(SRT/VTT/JSON) — captions, highlights and renders work the same either way.

### Walkthrough: the acceptance path

1. **Create a project** (`/projects/new`).
2. **Upload** a video in the project — it is validated, hashed, stored privately, probed, and given a
   thumbnail by a durable job; progress streams over SSE.
3. **Transcribe** or **import cues** — real word/segment timings land in the transcript table.
4. **Detect highlights** — deterministic scoring picks non-overlapping windows inside your duration
   limits and records a score breakdown plus an explanation for each.
5. **Approve a candidate** → an editable clip with its own caption track.
6. **Edit** in `/editor/[clipId]`: trim, aspect ratio, caption style (browser preview mirrors the
   server style model), logo/overlay, audio options.
7. **Render** → FFmpeg burns the captions and encodes the MP4; the export row keeps the exact
   settings snapshot.
8. **Download** through a short-lived signed URL, or **schedule** a post on the calendar.

---

## Configuration

All backend settings are environment variables prefixed `CF_`; see [`.env.example`](./.env.example)
for the full, commented list. The ones you will actually touch:

| Variable | Default | Why it matters |
| --- | --- | --- |
| `CF_SECRET_KEY` | insecure dev value | JWT + signed URLs; **must** be replaced in production |
| `CF_DATABASE_URL` | SQLite file | use `postgresql+psycopg://…` in production |
| `CF_STORAGE_BACKEND` | `local` | `s3` for S3/R2 with private buckets |
| `CF_TRANSCRIPTION_PROVIDER` | `faster_whisper` | `manual` = import-only deployments |
| `CF_AI_PROVIDER` | `none` | `ollama` / `openai_compatible` to enable model-assisted ranking and copy |
| `CF_SOCIAL_PUBLISH_ENABLED` | `true` | flip to `false` to hard-disable publishing (returns `publishing_disabled`) |
| `CF_ENABLE_INLINE_WORKER` | `false` | dev convenience: run one worker inside the API process |
| `API_PROXY_TARGET` | `http://127.0.0.1:8000` | where the Next.js server proxies `/api/v1` |

Nothing secret is ever exposed to the browser: the frontend talks to its own origin and the proxy
attaches the request server-side.

---

## API surface (all under `/api/v1`)

```
auth        register, login, logout, session, profile, password, organization
projects    CRUD, jobs, media
media       upload, URL import, get/delete, transcribe, transcript, transcript export
transcripts get, import, segment edit/create/delete, cues
candidates  generate, list, get, patch, approve, reject, rerank
clips       list/create, get, patch, delete, revisions, range-from-segments,
            captions (get/patch/preview/export), render, exports, download
content     generate (clip/project), list, patch, delete
brand-kits  CRUD, logo upload, caption presets
social      providers, connect/callback, accounts, posts, publish, schedule, calendar,
            attempts, metrics
jobs        list, get, cancel, SSE event stream
analytics   dashboard, overview, usage, exports
system      health, config, provider test, audit logs, account deletion
files       signed file serving (HMAC + expiry)
```

---

## Testing

```bash
cd apps/api
python -m pytest tests/ -q          # 75 tests
python -m ruff check app tests      # lint
```

Coverage highlights:

- `test_pipeline_e2e.py` — upload → import/transcribe → highlights → approve → edit → captions →
  render → signed download, with assertions on the produced MP4.
- `test_auth.py` — registration, duplicate emails, login failures, session/logout, CSRF
  enforcement, password rotation invalidating old credentials, honest transcription failure.
- `test_tenant_isolation.py` — cross-workspace reads/writes return 404, signed-URL tampering and
  expiry are rejected, audit logs are scoped.
- `test_upload_validation.py` — extension/MIME/signature/size checks, filename sanitisation,
  SSRF matrices for the URL importer.
- `test_highlights_ranking.py`, `test_captions.py` — scoring invariants, ASS centisecond
  timestamps, wrapping, presets, SRT/VTT parsing.
- `test_jobs_and_publishing.py` — cancellation, retry-then-fail, permanent errors, already-published
  protection, missing export/account failure paths, scheduling rules, idempotent publish jobs.

Frontend checks:

```bash
cd apps/web
npx tsc --noEmit && npx next lint && npm run build
```

---

## Security model

- Session = signed JWT in an `HttpOnly` cookie; writes additionally require the `X-CSRF-Token`
  header matching the `clipfoam_csrf` cookie (bearer clients are exempt by design).
- Every query is scoped by `organization_id`; cross-tenant access returns `404`.
- Uploads are validated by extension **and** container signature before any probe.
- URL imports resolve DNS and reject private/loopback/metadata ranges, credentials in URLs,
  non-HTTP schemes, oversized bodies and cross-host redirects.
- Media is private; access is granted only through HMAC-signed URLs with an expiry.
- OAuth tokens are encrypted at rest (Fernet, `CF_TOKEN_ENCRYPTION_KEY`).
- Audit log records actor, action, target and metadata for security-relevant events.
- Rate limits apply to the API and more aggressively to login.

---

## Roadmap (not implemented yet)

- Dockerfiles + Compose stack (API, worker, Postgres, Redis, web) and a Vercel deployment recipe.
- Longer-form docs: architecture decisions, provider cookbook, deployment and operations guides.
- Direct publishing for TikTok/Instagram/X/LinkedIn (blocked on platform app review in practice).
- A licensable music library and ducking automation.
- Motion-aware reframing (centre and face-aware crop exist today).
- Invitations, multi-user roles UI and per-seat quotas.
- CI workflow (GitHub Actions) running pytest + typecheck + build.

---

## Contributing

Issues and PRs welcome. Please keep the honesty contract: no fabricated provider responses, no
silent fallbacks, and any new integration must ship with its failure path and tests.

## License

MIT — see [LICENSE](./LICENSE).
