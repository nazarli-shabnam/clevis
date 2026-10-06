#!/bin/sh
set -e

# Creates a dedicated Postgres login role so the API can stop connecting as the initdb
# bootstrap superuser -- POSTGRES_USER always becomes a superuser on the official
# postgres image, and superusers unconditionally bypass Row-Level Security regardless of
# ENABLE/FORCE, which would make migrations 0030/0031/0033/0046's RLS policies enforce
# nothing in production. Unlike the worker (see 01-create-worker-role.sh), this role
# does NOT get BYPASSRLS -- the whole point is for the API to actually be subject to RLS.
#
# Runs via docker-entrypoint-initdb.d, so it only executes once, on a fresh data volume.
# Existing deployments must create this role manually -- see docs/self-hosting.md.
#
# No-op if API_DB_PASSWORD isn't set, keeping the shared-credential (superuser) fallback
# untouched. This script only grants table privileges once the role exists -- see
# migration 0032. The runtime cutover is in apps/api/entrypoint.sh, gated on the same
# API_DB_PASSWORD env var.
if [ -z "$API_DB_PASSWORD" ]; then
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v api_password="$API_DB_PASSWORD" -v db_name="$POSTGRES_DB" <<-'EOSQL'
-- psql's :'var' substitution doesn't reach inside a dollar-quoted string, so the DO
-- block is built as text via format()/%L (which also handles escaping the password
-- safely) and executed with \gexec, rather than interpolating :'api_password'
-- directly inside $do$...$do$.
SELECT format($fmt$
DO $do$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
    CREATE ROLE clevis_api WITH LOGIN PASSWORD %L;
  END IF;
END
$do$;
$fmt$, :'api_password') \gexec

GRANT CONNECT ON DATABASE :"db_name" TO clevis_api;
EOSQL
