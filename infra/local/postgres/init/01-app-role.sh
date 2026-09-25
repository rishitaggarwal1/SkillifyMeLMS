#!/usr/bin/env bash
# Creates the non-owner runtime role the API connects as. It is not a superuser, does not own any
# tables and has no BYPASSRLS, so Row-Level Security policies always apply to it.
#
# Runs automatically on first start of the docker-compose postgres container. CI runs it directly
# (with PGHOST/PGPASSWORD set) because GitHub service containers cannot use init scripts.
set -euo pipefail

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 -v app_password="$APP_DB_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SELECT format(
  'CREATE ROLE skillify_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
  :'app_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'skillify_app')\gexec
SQL
