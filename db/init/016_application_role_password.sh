#!/bin/sh
# Sets the application role's password from APP_DB_PASSWORD (role name from APP_DB_USER, default zm_terminal_app): the same values the
# API container receives, so the role and the API cannot drift. Runs after 015_application_role.sql, on first initialisation of an empty
# volume, and can be re-run on an existing volume with: docker compose exec -T postgres sh /docker-entrypoint-initdb.d/016_application_role_password.sh
# The password is NEVER a psql argument and never part of the script text: it stays in the environment and psql reads it itself with
# \getenv, then uses it only through :'pw' (proper literal quoting). The role name (not a secret) is a -v variable used as :"role".
set -eu
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set to initialise the application role}"
export APP_DB_PASSWORD  # psql inherits it; \getenv below reads it
psql -v ON_ERROR_STOP=1 -v role="${APP_DB_USER:-zm_terminal_app}" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv pw APP_DB_PASSWORD
-- Check first so the password-bearing statement below can only fail for reasons outside this script (a failed statement is logged by the server).
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') AS role_exists \gset
\if :role_exists
ALTER ROLE :"role" PASSWORD :'pw';
\else
DO $$ BEGIN RAISE EXCEPTION 'the application role does not exist: apply db/init/015_application_role.sql first'; END $$;
\endif
SQL
