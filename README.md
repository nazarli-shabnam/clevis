<div align="center">

# Clevis

### Analytics and security insight for GitHub repositories and organizations.

<br/>

[![CI](https://img.shields.io/github/actions/workflow/status/nazarli-shabnam/clevis/ci.yml?style=for-the-badge&logo=githubactions&logoColor=white&label=CI)](https://github.com/nazarli-shabnam/clevis/actions/workflows/ci.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/nazarli-shabnam/clevis/codeql.yml?style=for-the-badge&logo=github&logoColor=white&label=CodeQL)](https://github.com/nazarli-shabnam/clevis/actions/workflows/codeql.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-a855f7?style=for-the-badge)](LICENSE)
[![Self-host](https://img.shields.io/badge/Self--host-Docker_Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](#self-host)

<br/>

**Clevis turns the GitHub API into a dashboard.** Track repository activity, audit organization security posture, and run privileged maintenance jobs — from a single, dense, self-hosted control panel.

</div>

---

## What it does

| Area | Description |
|------|-------------|
| **Overview** | At-a-glance stats across your repositories and organization. |
| **Activity** | Repository and org event streams pulled from the GitHub API. |
| **Repositories** | Browse and inspect repositories with their key signals in one place. |
| **Health & Security** | A computed **security score** backed by automated checks — **MFA enforcement**, **branch protection**, and **secret scanning** — each with pass/fail status and remediation guidance. |
| **Collaborators** | See who has access across the surface you manage. |
| **Automation** | Run privileged maintenance jobs (e.g. clearing GitHub Actions caches) with **dry-run** support and a full **audit trail**. |

Every privileged action is recorded in an immutable audit log, and long-running work is dispatched to a background worker through a Postgres-backed job queue.

---

## Architecture

Clevis is three independently deployable services around one shared check library:

| Component | Stack | Responsibility |
|-----------|-------|----------------|
| **`apps/api`** | FastAPI · SQLAlchemy 2 · Alembic | REST backend — auth, analytics, RBAC, and job enqueueing. |
| **`apps/worker`** | Python · psycopg3 | Polls the job queue (`SELECT … FOR UPDATE SKIP LOCKED`) and executes GitHub API tasks. Scales to multiple replicas safely. |
| **`apps/ui`** | Next.js 15 · React 19 · TanStack Query | The dashboard — dense, dark, keyboard-friendly. |
| **`packages/checks`** | `clevis-checks` (Python) | The security-check engine (MFA, branch protection, secret scanning) with built-in GitHub pagination. |

**Security model:** Sessions are JWT-based (email/password or GitHub OAuth login). Access is org-scoped RBAC (`member` / `admin` per org, resolved fresh from the DB on every request) plus a small set of workspace-admin-only routes for instance-wide config. GitHub tokens are Fernet-encrypted whenever they're persisted — transiently in the job queue, or durably if a workspace admin chooses to save one for reuse — and only ever decrypted at the point of use. Every request carries a propagated `X-Request-ID` for traceability.

---

## Built with

<div align="center">

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)
<br/>
![Next.js](https://img.shields.io/badge/Next.js_15-000000?style=for-the-badge&logo=next.js&logoColor=white)
![React](https://img.shields.io/badge/React_19-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_v4-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)

</div>

---

## Self-host

**Prerequisites:** Docker + Docker Compose.

```bash
git clone https://github.com/nazarli-shabnam/clevis
cd clevis
cp .env.example .env        # fill in every variable — none have defaults
docker compose up --build -d
```

Then open the UI at **`http://localhost:3000`**.

> [!IMPORTANT]
> **CI secret required.** The Python test job needs a `JOB_SECRET_KEY` repository secret
> (*GitHub → Settings → Secrets and variables → Actions*). Generate one with `openssl rand -hex 32`.

<details>
<summary><b>Local development (without Docker)</b></summary>

```bash
# One-time setup
cp .env.example .env
pip install -r apps/api/requirements.txt
pip install -r requirements-test.txt
pip install -e packages/checks
cd apps/ui && bun install
```

Run each service in its own terminal:

```bash
docker compose up db                              # Postgres
cd apps/api && alembic upgrade head && uvicorn src.main:app --reload   # API  → :8080
cd apps/ui && bun run dev                          # UI   → :3000
cd apps/worker && python src/worker.py             # worker (optional)
```

Run the tests:

```bash
pytest -q                 # Python (hits a real Postgres; transaction-isolated)
cd apps/ui && bun run check   # UI: typecheck + lint + build
```

See [`CLAUDE.md`](CLAUDE.md) for full architecture and configuration notes.

</details>

---

## CI / CD

CI runs on **every pull request** and on **pushes to any branch**, verifying:

- UI TypeScript typecheck + production build
- Python source compilation
- Docker image builds for `api`, `worker`, and `ui`

On version tags (`v*`), images are published to the GitHub Container Registry so others can self-host without building from source:

```
ghcr.io/<owner>/clevis-api
ghcr.io/<owner>/clevis-worker
ghcr.io/<owner>/clevis-ui
```

[CodeQL](https://github.com/nazarli-shabnam/clevis/actions/workflows/codeql.yml) scans the codebase for security issues on a schedule.

---

## Contributing

Contributions are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first — commits follow [Conventional Commits](https://www.conventionalcommits.org/) (enforced via commitlint), and the design language for any UI work is documented in [`DESIGN.md`](DESIGN.md).

Found a security issue? Please follow the [security policy](SECURITY.md) rather than opening a public issue.

## License

Released under the [GNU General Public License v3.0](LICENSE).
