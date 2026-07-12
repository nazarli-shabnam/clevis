#!/bin/sh
set -e

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ] || [ -z "$DB_PASSWORD" ] || [ -z "$JOB_SECRET_KEY" ] || [ -z "$AUTH_SECRET" ]; then
  echo "ERROR: DB_USER, DB_NAME, DB_PASSWORD, JOB_SECRET_KEY, and AUTH_SECRET must all be set" >&2
  exit 1
fi

export DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}"
export AUTH_SECRET
python -m alembic upgrade head
# --proxy-headers/--forwarded-allow-ips: trust X-Forwarded-Proto from Traefik so
# request.url_for() (used to build the GitHub OAuth callback/redirect_uri) reports the
# original https scheme instead of the http uvicorn actually receives behind the proxy.
# Without this, GitHub rejects the OAuth flow with "redirect_uri is not associated with
# this application" since the registered callback is https but the sent one was http.
# '*' is safe here — Traefik and the API only ever talk over the internal Docker network,
# never a public one.
exec uvicorn src.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*'
