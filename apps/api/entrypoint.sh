#!/bin/sh
set -e

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ] || [ -z "$DB_PASSWORD" ] || [ -z "$JOB_SECRET_KEY" ] || [ -z "$AUTH_SECRET" ] || [ -z "$REDIS_PASSWORD" ]; then
  echo "ERROR: DB_USER, DB_NAME, DB_PASSWORD, JOB_SECRET_KEY, AUTH_SECRET, and REDIS_PASSWORD must all be set" >&2
  exit 1
fi

# Percent-encodes so URI delimiters in the value (@, :, /, %, #, ...) aren't
# misparsed as part of the connection URL's structure.
_urlenc() {
  python -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

_db_user_enc=$(_urlenc "$DB_USER")
_db_password_enc=$(_urlenc "$DB_PASSWORD")
_db_name_enc=$(_urlenc "$DB_NAME")
_redis_password_enc=$(_urlenc "$REDIS_PASSWORD")

# Built here so the raw password is set once. Must be set before `alembic upgrade
# head` below, since alembic/env.py imports src.core.config, which requires REDIS_URL.
export REDIS_URL="redis://:${_redis_password_enc}@redis:6379/0"

# Migrations run under the DB_USER superuser credential -- it owns the tables (table
# owners bypass RLS unless FORCE is set, see migration 0030) and needs DDL/GRANT
# privilege the runtime clevis_api role intentionally lacks.
export DATABASE_URL="postgresql+psycopg://${_db_user_enc}:${_db_password_enc}@db:5432/${_db_name_enc}"
export AUTH_SECRET
python -m alembic upgrade head

# Runtime app connects as a dedicated non-superuser clevis_api role when
# API_DB_PASSWORD is set, so RLS (migrations 0030/0031) actually applies -- DB_USER is
# the initdb bootstrap superuser and bypasses RLS regardless of ENABLE/FORCE. Falls
# back to sharing DB_USER/DB_PASSWORD when unset.
if [ -n "$API_DB_PASSWORD" ]; then
  export DATABASE_URL="postgresql+psycopg://clevis_api:$(_urlenc "$API_DB_PASSWORD")@db:5432/${_db_name_enc}"
fi

# Trust X-Forwarded-Proto from Traefik so request.url_for() (GitHub OAuth
# redirect_uri) reports https instead of the http uvicorn receives behind the proxy --
# otherwise GitHub rejects the callback. '*' is safe: Traefik and the API only talk
# over the internal Docker network.
exec uvicorn src.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*'
