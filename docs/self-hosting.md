# Self-hosting

This is the infrastructure/ops guide — getting the Clevis stack itself running and configured. For how to sign in and connect a GitHub org once the instance is up, see the [README](../README.md#get-started).

## Requirements

- Docker + Docker Compose

## Setup

1. Clone the repo and copy the env template:

   ```bash
   git clone https://github.com/nazarli-shabnam/clevis
   cd clevis
   cp .env.example .env
   ```

2. Fill in `.env`. Six vars are hard-required (the app fails to start without them) — `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `JOB_SECRET_KEY` (`openssl rand -hex 32`), `AUTH_SECRET` (`openssl rand -hex 32`), `NEXT_PUBLIC_API_BASE`. Everything else in `.env.example` has a safe default and only needs overriding per environment (e.g. `CORS_ORIGINS` for your real UI domain, `GITHUB_API_BASE` for GitHub Enterprise).

3. Register a GitHub App on github.com (**Settings → Developer settings → GitHub Apps → New GitHub App**) so users can connect their orgs after the instance is running:
   - **Homepage URL** — your deployed UI URL.
   - **Callback URL** — `<NEXT_PUBLIC_API_BASE>/auth/github/callback` (this exact path is required for "Sign in with GitHub" to work).
   - **Setup URL** (under "Post installation") — `<NEXT_PUBLIC_UI_BASE>/settings/github-callback`, with **"Redirect on update"** also checked. This is required for the **"Install GitHub App"** button on the Settings page to actually connect an org/account: without it, GitHub has nowhere to send the user back to after installation, so a `github_installations` row never gets created and the app silently behaves as if nothing was ever connected. If this field is left blank, GitHub falls back to redirecting to the Homepage URL instead — which won't complete the connection.
   - **Webhook** — optional, but recommended: point it at `<NEXT_PUBLIC_API_BASE>/webhooks/github`, generate a webhook secret, and set `GITHUB_APP_WEBHOOK_SECRET` in `.env` to the same value. Without it, `github_installations` rows are never cleaned up when a user uninstalls the App — they just go stale.
   - Grant read access to the repository/organization data the security checks need (contents, metadata, administration, members) — generate a private key once the App is created.
   - Copy the resulting values into `.env`: `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY` (the full `.pem` contents). Also set `NEXT_PUBLIC_GITHUB_APP_SLUG` to the App's slug (from its public page URL) — this powers the "Install GitHub App" button in the UI.

   **If you already registered the App before this change:** open the App's settings, go to the "Post installation" section, and add the Setup URL above — existing installs don't need to be redone, only new/updated ones will hit the new callback page.

4. Start the stack:

   ```bash
   docker compose up --build -d
   ```

   Or, on a tagged release, pull the pre-built images instead of building from source:

   ```
   ghcr.io/<owner>/clevis-api
   ghcr.io/<owner>/clevis-worker
   ghcr.io/<owner>/clevis-ui
   ```

5. Verify it's up:

   ```bash
   curl http://localhost:8080/healthz   # -> {"status": "ok"}
   ```

   Then open the UI and continue with the [Get started](../README.md#get-started) flow in the README.

## Security notes

- Sign-in uses a JWT held in an httpOnly session cookie, not a trusted request header — set `session_cookie_secure=false` only for local HTTP dev, never in production.
- Restrict API ingress behind your reverse proxy/SSO; the base `docker-compose.yml` deliberately publishes no host ports (Traefik-only) for this reason.
- Prefer GitHub App auth over the legacy personal-access-token path for anything beyond local testing — tokens are Fernet-encrypted at rest either way, but App installation tokens are short-lived and scoped per-org.

## Observability

- The API has request-ID-tagged logs and a health endpoint at `/healthz`.
- The worker logs each job's outcome (done/failed) as it processes the queue.
