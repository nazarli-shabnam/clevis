# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Clevis is a GitHub analytics dashboard with three independently deployable services and one shared Python library:

- **`apps/api`** — FastAPI REST backend (Python). Handles auth, analytics, RBAC, and job enqueueing. Uses SQLAlchemy 2 + Alembic against PostgreSQL.
- **`apps/worker`** — Standalone Python process. Polls the `jobs` table using `SELECT … FOR UPDATE SKIP LOCKED` and calls the GitHub API to execute background tasks (currently: clearing Actions cache). Uses raw psycopg3, not SQLAlchemy.
- **`apps/ui`** — Next.js 15 / React 19 frontend. Uses TanStack Query for data fetching, Tailwind v4, Base UI primitives, shadcn components.
- **`packages/checks`** — `clevis-checks` Python package. Defines `Check` base class and GitHub security checks (MFA, branch protection, secret scanning). Must be installed editable for `import checks` to work.

### Database models (defined in `apps/api/src/core/db.py`)

Three tables: `github_installations`, `audit_logs`, `jobs`. Schema is managed by Alembic — no runtime DDL.

### RBAC

Role is passed via `X-Role` HTTP header (`viewer` / `analyst` / `admin`). Default role from `settings.default_rbac_role`. Enforced with the `require_role()` dependency in `src/services/rbac.py`.

### Token encryption

GitHub tokens stored in jobs are encrypted with Fernet, keyed off `JOB_SECRET_KEY` (SHA-256 derived). The API encrypts (`src/core/_crypto.py`); the worker decrypts (`apps/worker/src/_crypto.py`).

### Database URL dialect

The API uses `postgresql+psycopg://` (SQLAlchemy dialect). The worker strips the `+psycopg` prefix to get a plain `postgresql://` URL for psycopg3's `connect()`.

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

Key variables to fill in:
- `DB_USER`, `DB_PASSWORD`, `DB_NAME` — Postgres credentials. Docker Compose maps these to `POSTGRES_USER/PASSWORD/DB` for the db container and the api/worker entrypoints construct `DATABASE_URL` from them (host = `db`).
- `DATABASE_URL` — local dev only (outside Docker); format: `postgresql+psycopg://<user>:<pass>@localhost:5432/<db>`. Not used inside Docker — entrypoints build it from `DB_*` vars.
- `JOB_SECRET_KEY` — generate with `openssl rand -hex 32`
- `GITHUB_API_BASE` — `https://api.github.com` for public GitHub
- `CORS_ORIGINS` — JSON list, e.g. `["http://localhost:3000"]`
- `DEFAULT_RBAC_ROLE` — `viewer`
- `WORKER_POLL_SECONDS` — `5`
- `DEBUG` — `false`
- `API_PORT` / `UI_PORT` — `8080` / `3000`
- `NEXT_PUBLIC_API_BASE` — `http://localhost:8080`

## Running locally

Each command runs in a separate terminal from the repo root:

```bash
# Terminal 1: Postgres
docker compose up db

# Terminal 2: API (PowerShell: run these as two separate commands)
cd apps/api
alembic upgrade head          # run after new migrations
uvicorn src.main:app --reload # http://localhost:8080

# Terminal 3: UI
cd apps/ui && npm run dev     # http://localhost:3000

# Terminal 4 (optional): worker
cd apps/worker && python src/worker.py
```

Full stack via Docker:
```bash
docker compose --profile backend --profile frontend up --build
```

Docker Compose profiles: `backend` (db + api + worker), `frontend` (db + ui), default = db only.

OpenAPI docs (`/docs`, `/redoc`) are only available when `DEBUG=true`.

## Commands

### Python tests
```bash
pytest -q                              # all tests (api + worker) from repo root
pytest -q apps/api/tests/test_health.py  # single file
```

Tests hit a real Postgres database — no mocks. `pytest.ini` adds `apps/api` and `apps/worker/src` to `pythonpath`.

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
