# Contributing to clevis

Thanks for helping improve clevis. This document describes how to work in the repo so your changes match what CI expects.

## Project layout

| Path | Role |
|------|------|
| `apps/ui` | Next.js UI (Node 22, Bun) |
| `apps/api` | Python API |
| `apps/worker` | Python worker |
| `packages/checks` | `clevis-checks` shared Python library |
| `docker-compose.yml` | Full-stack Docker Compose (profiles: backend, frontend) |

## Prerequisites

- **Node.js 22** (same as CI)
- **Bun** (same as CI; used for `apps/ui` installs/scripts)
- **Python 3.12** (for API/worker checks)
- **Docker** (for image build verification)

## Initial setup

1. Clone the repository.
2. From the **repository root**, install tooling and enable Git hooks:

   ```bash
   npm ci
   ```

   The `prepare` script registers Husky. After this, commits run local checks (see below).

3. Install UI dependencies when working on the frontend:

   ```bash
   cd apps/ui
   bun install --frozen-lockfile
   ```

## Line endings

The repo uses `.gitattributes` so text files are stored with **LF**. On Windows, let Git handle normalization (`core.autocrlf` is usually fine with `true` or `input`). Shell hook scripts (`.husky/*`) should stay LF.

## Git hooks (Husky)

After `npm ci` at the **repository root**, Git uses Husky (`core.hooksPath` → `.husky/_`). Then:

- **`pre-commit`** runs before the commit is recorded. It runs `bun run typecheck` in `apps/ui`. Install UI deps (`cd apps/ui && bun install --frozen-lockfile`) so this can succeed.
- **`commit-msg`** runs **immediately after** you supply a message—whether from `git commit -m "your subject"` or from the editor. It runs the same [Commitlint](https://commitlint.js.org/) rules as CI ([Conventional Commits](https://www.conventionalcommits.org/) via `@commitlint/config-conventional`), with **`--verbose`** output like the workflow.

If Commitlint fails, **Git does not create the commit**. You will see the errors in your terminal; fix the message and run `git commit` again (no need to wait for CI to discover the problem).

**Hooks not running?** Run `npm ci` (or `npm install`) once at the repo root. If you only install dependencies under `apps/ui`, root Husky never runs and commits will not be checked locally.

(This root install is npm, not Bun — it's only for the tiny commitlint/Husky devDependency set at the repository root and is unrelated to the `apps/ui` Bun migration.)

To bypass hooks in exceptional cases (not recommended for routine work): `git commit --no-verify`.

**Dry-run a message without committing** (from repo root, after `npm ci`):

```bash
printf '%s\n' 'feat(ui): example valid message' | npx --no -- commitlint --verbose
```

## Commit messages

Use **Conventional Commits**, for example:

- `feat(ui): add repo filter to sidebar`
- `fix(api): handle rate-limit responses`
- `chore: bump worker deps`

CI runs Commitlint on the commits in each push or pull request, so messages that do not follow the convention will fail the **Commit Messages** check.

## Testing

**Bug-fix PRs must include a regression test** that exercises the failure scenario described in the linked issue — not just a description or manual test-plan checklist. A fix without a test that would have caught the original bug is not considered complete.

Two established patterns to follow, depending on what you're testing:

- **Pure-function unit test** — for logic extracted into a small helper (e.g. `apps/ui/lib/repo-segment.ts`, `apps/ui/lib/token-resolve.ts`, or a Python function). Plain `describe`/`it` (UI) or a `test_*` function (Python), no rendering or mocking needed. See `apps/ui/tests/lib/repo-segment.test.ts` or `apps/ui/tests/lib/token-resolve.test.ts`.
- **Hook/component test with mocked I/O** — for behavior that lives in a React hook or component (e.g. `useAuth`, `AuthGuard`) or a Python function that hits the network/DB. On the UI side, use `renderHook`/`render` from `@testing-library/react` with `vi.stubGlobal("fetch", ...)` or `vi.mock(...)` to control responses — see `apps/ui/tests/lib/auth-context.test.tsx` or `apps/ui/tests/components/auth-guard.test.tsx`. On the Python side, tests run against a real Postgres database inside a transaction/savepoint (see `apps/api/tests/conftest.py`) rather than mocking the DB.

Run the full test suites before opening a PR:

```bash
# UI, from apps/ui
bun run test

# Python (API + worker + packages/checks), from repo root
pytest -q
```

### Coverage — enforced in CI

CI enforces two coverage gates, both self-hosted (no external service):

1. **Global floor** — a regression guard, not a target. Overall coverage can't drop below the last-measured baseline (currently ~85% Python, ~21% UI — the UI number is low because most `app/**` page components aren't unit-tested yet, not because the gate is lenient). This just stops things from getting worse.
2. **Diff coverage** — the real gate. New or changed lines in your PR must be covered by a test (currently `--fail-under=90`), checked against `origin/main` via [`diff-cover`](https://github.com/Bachmann1234/diff-cover). This is what actually enforces the rule above ("bug-fix PRs must include a regression test") — it will fail your PR if you touch a line with no test exercising it, regardless of the file's pre-existing coverage.

Check both locally before opening a PR:

```bash
# Python — from repo root, with the local Postgres db running (see `docker compose up db`)
pytest -q --cov=apps/api/src --cov=apps/worker/src --cov=packages/checks/src --cov-report=xml --cov-report=term
diff-cover coverage.xml --compare-branch=origin/main --fail-under=90

# UI — from apps/ui
bun run test:coverage
# diff-cover needs lcov.info's paths to be repo-root-relative (vitest emits them relative
# to apps/ui); rewrite them and run from the repo root:
cd .. && sed -E 's#^SF:(.*)$#SF:apps/ui/\1#' apps/ui/coverage/lcov.info | tr '\134' '/' > apps/ui/coverage/lcov-root-relative.info
diff-cover apps/ui/coverage/lcov-root-relative.info --compare-branch=origin/main --fail-under=90
```

`pip install diff-cover` if you don't have it (it's in `requirements-test.txt` already for the Python side).

## Checks to run before opening a PR

These mirror [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

### UI

```bash
cd apps/ui
bun install --frozen-lockfile
bun run typecheck
bun run lint
bun run test:coverage
bun run build
```

(`bun run check` runs typecheck, lint, and build together — but not tests; run those separately. See [Coverage](#coverage--enforced-in-ci) above for the diff-coverage check CI also runs.)

### Python (API + worker)

```bash
python -m pip install --upgrade pip
python -m pip install -r apps/api/requirements.txt
python -m pip install -r apps/worker/requirements.txt
python -m pip install -e packages/checks
python -m pip install -r requirements-test.txt
python -m pytest -q --cov=apps/api/src --cov=apps/worker/src --cov=packages/checks/src --cov-report=xml --cov-report=term --cov-fail-under=85
python -m compileall apps/api/src apps/worker/src
```

### Docker images

From the **repository root** (both `apps/api` and `apps/worker` build from the repo root so their images can install the shared `packages/checks` dependency; `apps/ui` has no such dependency and builds from its own directory):

```bash
docker build -t clevis-api -f apps/api/Dockerfile .
docker build -t clevis-worker -f apps/worker/Dockerfile .
docker build -t clevis-ui -f apps/ui/Dockerfile apps/ui
```

CI also smoke-tests the API and worker images after building — it runs each image's entrypoint module directly (`import src.main` / `import worker`) with dummy env vars, bypassing `entrypoint.sh` so no live DB is required. This catches a class of bug that a plain `docker build` can't: an image that builds fine but is missing a runtime dependency (e.g. `packages/checks` not being installed), which only surfaces once the container actually runs. If you change either Dockerfile, run the equivalent locally before opening a PR:

```bash
docker run --rm --entrypoint python \
  -e DATABASE_URL=postgresql+psycopg://smoke:smoke@localhost:5432/smoke \
  -e JOB_SECRET_KEY=local-smoke-test-key -e AUTH_SECRET=local-smoke-test-secret \
  clevis-api -c "import src.main"

docker run --rm --entrypoint python \
  -e DATABASE_URL=postgresql://smoke:smoke@localhost:5432/smoke \
  -e JOB_SECRET_KEY=local-smoke-test-key \
  clevis-worker -c "import worker"
```

## Pull requests

- Open a PR against the branch your maintainers use as the integration target (often `main`).
- Keep changes focused; unrelated drive-by refactors make review harder.
- Ensure all CI jobs pass (commits, UI, Python, Docker build + smoke-test).
- Bug-fix PRs should include a regression test — see [Testing](#testing) above.

## Security and configuration

- Do not commit secrets. Use `.env` locally (see `.env.example` and the main README for self-host setup).
- If you change ignore rules, remember that a bare `lib/` entry in `.gitignore` ignores **every** `lib` directory in the tree; root-only patterns like `/lib/` avoid accidentally excluding app code (for example under `apps/ui/lib`).

## Questions

If something in this doc is outdated or unclear, opening an issue or a small doc-fix PR is welcome.
