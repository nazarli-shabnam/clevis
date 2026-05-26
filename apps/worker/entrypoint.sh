#!/bin/sh
set -e

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ] || [ -z "$DB_PASSWORD" ] || [ -z "$JOB_SECRET_KEY" ]; then
  echo "ERROR: DB_USER, DB_NAME, DB_PASSWORD, and JOB_SECRET_KEY must all be set" >&2
  exit 1
fi

export DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}"
exec python src/worker.py
