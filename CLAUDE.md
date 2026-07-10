# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Clevis is a GitHub analytics dashboard with three independently deployable services and one shared Python library:

- **`apps/api`** — FastAPI REST backend (Python). Handles auth, analytics, RBAC, and job enqueueing. Uses SQLAlchemy 2 + Alembic against PostgreSQL.
- **`apps/worker`** — Standalone Python process. Polls the `jobs` table using `SELECT … FOR UPDATE SKIP LOCKED` and calls the GitHub API to execute background tasks (currently: clearing Actions cache). Uses raw psycopg3, not SQLAlchemy.
- **`apps/ui`** — Next.js 15 / React 19 frontend. Uses TanStack Query for data fetching, Tailwind v4, Base UI primitives, shadcn components.
- **`packages/checks`** — `clevis-checks` Python package. Defines `Check` base class and GitHub security checks (MFA, branch protection, secret scanning). Must be installed editable for `import checks` to work.

### Database models (`apps/api/src/core/db.py`)

Managed by Alembic — no runtime DDL. Core tables:
- **`users`** — email/password (bcrypt) or GitHub-OAuth-linked accounts. `is_workspace_admin` marks the instance-level admin (the first user ever created); everyone else is a regular user whose access is scoped per-org via `org_memberships`.
- **`orgs`** / **`org_memberships`** — the multi-tenant model (added in migration `0009_add_org_rbac.py`). `org_memberships.role` is `member` or `admin`, scoped per org.
- **`invitations`** — pending/accepted/revoked org invites; a shareable link (`/invite/{token}`) that grants membership to whoever accepts it with a matching email. Currently has no expiry — see open issues.
- **`github_installations`** — account login, installation ID, auth mode, and `token_ref` (a symbolic reference like `tok_acme`, not the actual token); scoped to exactly one of `org_id` or `owner_user_id` (enforced by a DB check constraint).
- **`saved_tokens`** — Fernet-encrypted GitHub PATs saved per org for reuse, so a workspace admin doesn't have to paste a token on every request. See "Token encryption" below.
- **`audit_logs`** — immutable audit trail; every significant action (cache clear, dry-run, etc.) writes here with actor, action, target, and payload JSON.
- **`jobs`** — job queue; composite index on `(status, job_type)` for efficient worker polling. Status lifecycle: `queued → processing → done/failed`. The `result` column stores JSON on success or a raw exception string on failure.

### Job queue flow

**Enqueueing (API):** `POST /orgs/{org_login}/repos/{owner}/{repo}/actions-caches/clear` (org-scoped) or `POST /me/repos/{owner}/{repo}/actions-caches/clear` (personal-scope, caller supplies their own token) — if `dry_run=true`, only an audit log is written; if `dry_run=false`, the token is Fernet-encrypted and a `jobs` row with `status='queued'` is inserted.

**Processing (worker):** atomic `SELECT FOR UPDATE SKIP LOCKED` claims one job, updates status to `processing`, decrypts the token, calls the GitHub API, then updates to `done` or `failed`. Multiple worker replicas can poll safely.

### Auth & RBAC

Sessions are JWTs (`AUTH_SECRET`, HS256, 30-day expiry), issued on email/password login (`POST /auth/login`) or GitHub OAuth (`GET /auth/github/callback`), and carried either as a `Bearer` header (API clients) or an httpOnly `clevis_session` cookie (browser). There are two independent access layers, defined in `apps/api/src/core`:

- **`core/auth.py`** — `require_auth` (any valid JWT) and `require_workspace_admin` (JWT claims only, no DB hit — `is_workspace_admin` is baked into the token at issuance, so revoking/demoting a user does not take effect until their token expires).
- **`core/rbac.py`** — `require_org_role(min_role)` (`member` / `admin`), which resolves the caller's `OrgMembership` fresh from the DB on every request, since org access can change while a JWT is still valid. `assert_owner_matches_org` additionally guards that a repo-level `owner` path/body value matches the org context the caller was authorized for.

Most routes are org-scoped (`/orgs/{org_login}/...`, `require_org_role`) or personal-scope (`/me/...`, `require_auth` only — the caller brings their own GitHub token, so there's no installation to authorize against). Workspace-admin-only routes (`/tokens`, `/jobs`, `/audit`, `/config`) manage instance-wide state, not per-org data.

### Token encryption

GitHub PATs are Fernet-encrypted at rest in two places: transiently in `jobs.payload` while a cache-clear job is queued, and persistently in `saved_tokens.encrypted_token` when a workspace admin saves an org's token for reuse (`POST /tokens/{org}`). The Fernet key is derived from `JOB_SECRET_KEY` via SHA-256 (base64-encoded) — both `apps/api` and `apps/worker` duplicate the same `_crypto.py` logic. `GET /tokens` never returns raw tokens; `POST /tokens/resolve` does (workspace-admin only), for cases where the raw PAT needs to be reused client-side.

### Database URL dialect

The API uses `postgresql+psycopg://` (SQLAlchemy dialect). The worker strips the `+psycopg` prefix to obtain a plain `postgresql://` URL for psycopg3's native `connect()`.

### Analytics & checks integration

`analytics_service.py` calls `checks.runner.run_all_checks(owner, token, base_url)`, which instantiates and runs the three `Check` subclasses in `packages/checks`. Score is computed as `100 - (failed_count / total_count * 100)`. Each check returns `{"status": "pass"|"fail", "value": <object>}`. The checks package handles GitHub API pagination internally via Link header parsing — callers receive complete results.

### Request ID propagation

`RequestIdMiddleware` (`apps/api/src/core/middleware.py`) assigns a UUID to every request (or forwards `X-Request-ID` if present), stores it in a `ContextVar`, and injects it into all log records. The ID is echoed in the `X-Request-ID` response header.

### UI routing

Owner and repo are joined with `~` in URL dynamic segments (e.g., `/repos/octocat~hello-world/cache`). The API client lives in `apps/ui/lib/api/client.ts`; it centralises JSON serialisation, error parsing, and attaches the session (via the httpOnly cookie sent automatically on same-site credentialed requests, or a `Bearer` header where applicable).

## Development setup

```bash
# One-time setup
cp .env.example .env           # fill in ALL variables — none have defaults
pip install -r apps/api/requirements.txt
pip install -r requirements-test.txt
pip install -e packages/checks
cd apps/ui && bun install
```

**Required env vars (6 total):** `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `JOB_SECRET_KEY`, `AUTH_SECRET`, `NEXT_PUBLIC_API_BASE`. Two more deploy-time vars are optional (safe defaults in code): `CORS_ORIGINS`, `GITHUB_API_BASE`. Everything else lives in the `app_config` DB table (configured via Settings page).

Key variables:
- `DB_USER`, `DB_PASSWORD`, `DB_NAME` — Postgres credentials. Docker Compose maps these to `POSTGRES_USER/PASSWORD/DB` for the db container; entrypoints construct `DATABASE_URL` from them (host = `db`).
- `DATABASE_URL` — local dev only (outside Docker); format: `postgresql+psycopg://<user>:<pass>@localhost:5432/<db>`.
- `JOB_SECRET_KEY` — Fernet key for token encryption; generate with `openssl rand -hex 32`
- `AUTH_SECRET` — JWT signing secret; generate with `openssl rand -hex 32`
- `NEXT_PUBLIC_API_BASE` — `http://localhost:8080` for local dev
- `API_PORT` / `UI_PORT` — `8080` / `3000`

**Deploy-time config (env vars, safe defaults in code):**
- `CORS_ORIGINS` — JSON array of allowed origins; default `["http://localhost:3000"]`. Read once at API startup (a security boundary), so a change requires an API restart. Set your real UI domain in production.
- `GITHUB_API_BASE` — default `https://api.github.com`; set for GitHub Enterprise (e.g. `https://github.yourco.com/api/v3`). Used by both the API and the worker. Not runtime-editable because it's where GitHub tokens are sent.

**DB-backed config (editable in Settings → Instance Configuration):**
- `worker_poll_seconds` — default `5`; the worker re-reads it each loop, so changes take effect live without a restart

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
cd apps/ui && bun run dev      # http://localhost:3000

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
bun run dev          # dev server
bun run typecheck    # tsc --noEmit
bun run lint         # eslint
bun run test         # vitest run
bun run check        # typecheck + lint + build
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
