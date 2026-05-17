#!/bin/sh
set -e

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ] || [ -z "$DB_PASSWORD" ]; then
  echo "ERROR: DB_USER, DB_NAME, and DB_PASSWORD must all be set in .env" >&2
  exit 1
fi

export DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}"
python -m alembic upgrade head
exec uvicorn src.main:app --host 0.0.0.0 --port 8080
