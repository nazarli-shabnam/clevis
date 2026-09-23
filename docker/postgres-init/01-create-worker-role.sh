#!/bin/sh
set -e

# Creates a dedicated Postgres login role for the worker so a future migration can grant
# it BYPASSRLS independently of the API's role -- granting BYPASSRLS to a role shared
# with the API would make RLS a no-op for the API too.
#
# Runs via docker-entrypoint-initdb.d, so it only executes once, on a fresh data volume.
# Existing deployments must create this role manually -- see docs/self-hosting.md.
#
# No-op if WORKER_DB_PASSWORD isn't set, keeping the shared-credential fallback untouched.
if [ -z "$WORKER_DB_PASSWORD" ]; then
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v worker_password="$WORKER_DB_PASSWORD" -v db_name="$POSTGRES_DB" <<-'EOSQL'
-- psql's :'var' substitution doesn't reach inside a dollar-quoted string, so the DO
-- block is built as text via format()/%L (which also handles escaping the password
-- safely) and executed with \gexec, rather than interpolating :'worker_password'
-- directly inside $do$...$do$.
SELECT format($fmt$
DO $do$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
    CREATE ROLE clevis_worker WITH LOGIN PASSWORD %L;
  END IF;
END
$do$;
$fmt$, :'worker_password') \gexec

GRANT CONNECT ON DATABASE :"db_name" TO clevis_worker;
EOSQL
