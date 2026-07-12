# AGENTS.md

This is the shared, tool-agnostic guide for working in this repository — for any AI coding agent (Claude Code, Cursor, GitHub Copilot, Gemini CLI, or others) and for humans. It is the single source of truth; tool-specific files (`CLAUDE.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`) point here rather than duplicating content.

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

## Guardrails for AI Agents

These apply to every AI tool working in this repo, not just Claude Code.

### 1. Migrations require deliberate care, never rushed

This is the highest-priority guardrail in this file.

- Before creating a new migration file, stop and think hard about whether a schema change is actually necessary for the task — don't generate one reflexively or "just in case." Unnecessary migration files make contributors' PRs noisy and hard to review.
- If a schema change genuinely is required, run `alembic revision --autogenerate`, then read the generated diff carefully before running `upgrade head`. Autogenerate can miss renames (emits drop+add, losing data), miss server-side defaults, or pick up unrelated drift from a stale local DB.
- Never run `alembic downgrade` against a real environment without explicit user confirmation.
- Never hand-edit a migration file that has already been applied anywhere (including a teammate's local environment) — write a new migration instead.
- If a migration touches a column with existing data (type change, `NOT NULL`, drop), state the data-loss/backfill risk explicitly before running it, even if not asked.

### 2. Don't fabricate facts

Verify before asserting. Read the actual file or grep the repo before claiming a function, config var, endpoint, or library behavior exists — don't infer from naming conventions or from how a "similar" codebase might work.

This repo has sharp edges worth double-checking rather than assuming:
- 6 required env vars have no defaults — the app hard-fails without them (see Development setup above).
- The `X-Role` RBAC header is trusted, not verified — there is no JWT/session validating it.
- Fernet token-encryption logic (`_crypto.py`) is duplicated between `apps/api` and `apps/worker` and must be kept in sync manually.

### 3. Other irreversible actions require explicit confirmation

No `git push --force`. No dropping or truncating tables. No bypassing `require_role("admin")` on the cache-clear endpoint. No committing `.env` or real tokens/secrets.

### 4. Don't scope-creep

A bug fix doesn't need surrounding cleanup. Don't touch files outside what was asked. Don't add abstractions for hypothetical future requirements. Most damage from AI agents in a mature codebase comes from unrequested "improvements," not from wrong facts — keep changes scoped to the task.

## AI Attribution Policy

- All AI-authored commits use a `Co-Authored-By: <Agent Name> <noreply@...>` trailer, regardless of which tool made the commit.
- PR descriptions for AI-assisted work must give a **detailed explanation of what changed and why** — not a summary of the diff, but the reasoning behind it — for every change included.
- **If a PR includes a new or modified migration file, the description MUST explicitly state that a schema change is included and explain why it was necessary.** This is non-negotiable: migrations are the highest-risk category of change in this repo (real Postgres, no rollback safety net) and must never be silently bundled into a PR.
- If a PR touches other sensitive files (`.env`/config, RBAC/auth code, token encryption logic), the description must explicitly flag this too, with reasoning.
- No inline code comments claiming AI authorship (e.g. `// generated by AI`). Attribution belongs in commit/PR metadata, not source files.
- AI-authored changes still require human review before merge. An agent's commit is not a substitute for a reviewer.
