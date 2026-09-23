#!/bin/sh
set -e

if [ -z "$DB_NAME" ] || [ -z "$JOB_SECRET_KEY" ] || [ -z "$REDIS_PASSWORD" ]; then
  echo "ERROR: DB_NAME, JOB_SECRET_KEY, and REDIS_PASSWORD must be set" >&2
  exit 1
fi

# Percent-encodes so URI delimiters in the value (@, :, /, %, #, ...) aren't
# misparsed as part of the connection URL's structure.
_urlenc() {
  python -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.quote_plus(sys.argv[1]))' "$1"
}

_db_name_enc=$(_urlenc "$DB_NAME")

# Built here rather than a directly-set REDIS_URL so the raw password only needs
# setting once (same as apps/api/entrypoint.sh's REDIS_URL export).
export REDIS_URL="redis://:$(_urlenc "$REDIS_PASSWORD")@redis:6379/0"

# Prefer a dedicated worker DB role over the credential shared with the API -- see
# docker/postgres-init/01-create-worker-role.sh for how it's provisioned.
if [ -n "$WORKER_DB_PASSWORD" ]; then
  export DATABASE_URL="postgresql+psycopg://clevis_worker:$(_urlenc "$WORKER_DB_PASSWORD")@db:5432/${_db_name_enc}"
elif [ -n "$DB_USER" ] && [ -n "$DB_PASSWORD" ]; then
  export DATABASE_URL="postgresql+psycopg://$(_urlenc "$DB_USER"):$(_urlenc "$DB_PASSWORD")@db:5432/${_db_name_enc}"
else
  echo "ERROR: either WORKER_DB_PASSWORD or (DB_USER and DB_PASSWORD) must be set" >&2
  exit 1
fi

exec python src/worker.py
