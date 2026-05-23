# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Clevis is a GitHub analytics dashboard with three independently deployable services and one shared Python library:

- **`apps/api`** — FastAPI REST backend (Python). Handles auth, analytics, RBAC, and job enqueueing. Uses SQLAlchemy 2 + Alembic against PostgreSQL.
- **`apps/worker`** — Standalone Python process. Polls the `jobs` table using `SELECT … FOR UPDATE SKIP LOCKED` and calls the GitHub API to execute background tasks (currently: clearing Actions cache). Uses raw psycopg3, not SQLAlchemy.
- **`apps/ui`** — Next.js 15 / React 19 frontend. Uses TanStack Query for data fetching, Tailwind v4, Base UI primitives, shadcn components.
- **`packages/checks`** — `clevis-checks` Python package. Defines `Check` base class and GitHub security checks (MFA, branch protection, secret scanning). Must be installed editable for `import checks` to work.

### Database models (`apps/api/src/core/db.py`)

Three tables managed by Alembic — no runtime DDL:
- **`github_installations`** — account login, installation ID, auth mode, and `token_ref` (a symbolic reference like `tok_acme`, not the actual token).
- **`audit_logs`** — immutable audit trail; every significant action (cache clear, dry-run, etc.) writes here with actor, action, target, and payload JSON.
- **`jobs`** — job queue; composite index on `(status, job_type)` for efficient worker polling. Status lifecycle: `queued → processing → done/failed`. The `result` column stores JSON on success or a raw exception string on failure.

### Job queue flow

**Enqueueing (API):** `POST /repos/{owner}/{repo}/actions-caches/clear` — if `dry_run=true`, only an audit log is written; if `dry_run=false`, the token is Fernet-encrypted and a `jobs` row with `status='queued'` is inserted.

**Processing (worker):** atomic `SELECT FOR UPDATE SKIP LOCKED` claims one job, updates status to `processing`, decrypts the token, calls the GitHub API, then updates to `done` or `failed`. Multiple worker replicas can poll safely.

### RBAC

Role passed via `X-Role` HTTP header (`viewer` / `analyst` / `admin`). Defaults to `settings.default_rbac_role`. Enforced via `require_role()` FastAPI dependency (`src/services/rbac.py`). Only the cache-clear endpoint requires `admin`; all other routes are unrestricted. There is no session or JWT — the header is trusted to come from an auth proxy.

### Token encryption

Tokens are never stored persistently. When a job is enqueued the API encrypts the token with Fernet (key derived via SHA-256 of `JOB_SECRET_KEY`, base64-encoded). The worker decrypts it at processing time. Both sides duplicate the same `_crypto.py` logic.

### Database URL dialect

The API uses `postgresql+psycopg://` (SQLAlchemy dialect). The worker strips the `+psycopg` prefix to obtain a plain `postgresql://` URL for psycopg3's native `connect()`.

### Analytics & checks integration

`analytics_service.py` calls `checks.runner.run_all_checks(owner, token, base_url)`, which instantiates and runs the three `Check` subclasses in `packages/checks`. Score is computed as `100 - (failed_count / total_count * 100)`. Each check returns `{"status": "pass"|"fail", "value": <object>}`. The checks package handles GitHub API pagination internally via Link header parsing — callers receive complete results.

### Request ID propagation

`RequestIdMiddleware` (`apps/api/src/core/middleware.py`) assigns a UUID to every request (or forwards `X-Request-ID` if present), stores it in a `ContextVar`, and injects it into all log records. The ID is echoed in the `X-Request-ID` response header.

### UI routing

Owner and repo are joined with `~` in URL dynamic segments (e.g., `/repos/octocat~hello-world/cache`). The API client lives in `apps/ui/lib/api/client.ts`; it centralises JSON serialisation, error parsing, and `X-Role` header injection.

## Development setup

```bash
# One-time setup
cp .env.example .env           # fill in ALL variables — none have defaults
pip install -r apps/api/requirements.txt
pip install -r requirements-test.txt
pip install -e packages/checks
cd apps/ui && npm install
```

Every variable in `.env.example` is required — Settings classes have no defaults. Missing vars cause a `ValidationError` at startup.

Key variables:
- `DB_USER`, `DB_PASSWORD`, `DB_NAME` — Postgres credentials. Docker Compose maps these to `POSTGRES_USER/PASSWORD/DB` for the db container; entrypoints construct `DATABASE_URL` from them (host = `db`).
- `DATABASE_URL` — local dev only (outside Docker); format: `postgresql+psycopg://<user>:<pass>@localhost:5432/<db>`.
- `JOB_SECRET_KEY` — generate with `openssl rand -hex 32`
- `GITHUB_API_BASE` — `https://api.github.com` for public GitHub
- `CORS_ORIGINS` — JSON list, e.g. `["http://localhost:3000"]`
- `DEFAULT_RBAC_ROLE` — `viewer`
- `WORKER_POLL_SECONDS` — `5`
- `DEBUG` — `false`; enables `/docs` and `/redoc`
- `API_PORT` / `UI_PORT` — `8080` / `3000`
- `NEXT_PUBLIC_API_BASE` — `http://localhost:8080`

## Running locally

Each command runs in a separate terminal from the repo root:

```bash
# Terminal 1: Postgres
docker compose up db

# Terminal 2: API (PowerShell: run as two separate commands)
cd apps/api
alembic upgrade head
uvicorn src.main:app --reload  # http://localhost:8080

# Terminal 3: UI
cd apps/ui && npm run dev      # http://localhost:3000

# Terminal 4 (optional): worker
cd apps/worker && python src/worker.py
```

Full stack via Docker:
```bash
docker compose --profile backend --profile frontend up --build
```

Docker Compose profiles: `backend` (db + api + worker), `frontend` (db + ui), default = db only.

## Commands

### Python tests
```bash
pytest -q                                    # all tests from repo root
pytest -q apps/api/tests/test_health.py     # single file
```

Tests hit a real Postgres database — no mocks. `pytest.ini` adds `apps/api` and `apps/worker/src` to `pythonpath`. Each test function runs inside a transaction with a savepoint; the savepoint is rolled back after the test, giving a clean DB state without truncating tables.

### UI
```bash
cd apps/ui
npm run dev          # dev server
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm run test         # vitest run
npm run check        # typecheck + lint + build
```

### Database migrations
```bash
cd apps/api
alembic revision --autogenerate -m "description"
alembic upgrade head
```

Migration files live in `apps/api/alembic/versions/`. Use zero-padded 4-digit prefixes (e.g. `0003_...`).

## Commit conventions

Conventional Commits are enforced via commitlint + husky. Format: `type(scope): subject`. Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`. Merge commits are excluded from linting.
