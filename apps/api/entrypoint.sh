#!/bin/sh
set -e

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ] || [ -z "$DB_PASSWORD" ] || [ -z "$AUTH_SECRET" ]; then
  echo "ERROR: DB_USER, DB_NAME, DB_PASSWORD, and AUTH_SECRET must all be set" >&2
  exit 1
fi

export DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}"
export AUTH_SECRET
python -m alembic upgrade head
exec uvicorn src.main:app --host 0.0.0.0 --port 8080
