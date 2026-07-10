# Self-hosting

## Requirements
- Docker + Docker Compose
- GitHub App credentials (or PAT for testing)

## Setup
1. Copy `.env.example` to `.env` and update values.
2. Create local persistent directory `data/` in repo root.
3. Start stack:
   - `docker compose up --build -d`

## Security notes
- Use GitHub App auth in production, not PAT.
- Restrict API ingress behind your reverse proxy and SSO.
- Set `CORS_ORIGINS` to your real UI origin(s) before going live — it's a security boundary (credentialed CORS + the session cookie), read once at API startup, so changing it requires a restart.
- The first account created via `/auth/setup` becomes the workspace admin. Access beyond that is granted per-org (invite links from an org admin), or instance-wide for workspace-admin-only routes (`/tokens`, `/jobs`, `/audit`, `/config`).
- Generate `AUTH_SECRET` and `JOB_SECRET_KEY` with `openssl rand -hex 32` each — don't reuse one for the other, and don't commit real values.

## Observability
- API has request-id logs and health endpoint at `/healthz`.
- Worker logs completed/failed jobs for cache deletion.
