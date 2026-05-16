# clevis

Basic analytics for GitHub repos and organizations using the GitHub API.

## Self-host

### Prerequisites
- Docker + Docker Compose

### Setup
1. Copy `.env.example` to `.env` and fill in all variables
2. Start the stack:
   `docker compose up --build -d`

Open the UI (default: `http://localhost:3000`).

> **GitHub Actions secret required:** CI Python tests need `JOB_SECRET_KEY` set as a repository secret in GitHub → Settings → Secrets and variables → Actions. Generate a value with `openssl rand -hex 32`.

## CI / CD
CI runs on **every pull request** and on **pushes to any branch** and verifies:
- UI TypeScript typecheck + production UI build
- Python source compilation
- Docker image build for `api`, `worker`, and `ui`

On version tags (`v*`), Docker images are published to GHCR so others can self-host without rebuilding from source:
- `ghcr.io/<owner>/<repo>-api`
- `ghcr.io/<owner>/<repo>-worker`
- `ghcr.io/<owner>/<repo>-ui`

