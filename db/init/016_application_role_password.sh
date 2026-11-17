#!/bin/sh
# Sets the application role's password from APP_DB_PASSWORD (role name from APP_DB_USER, default zm_terminal_app): the same values the
# API container receives, so the role and the API cannot drift. Runs after 015_application_role.sql, on first initialisation of an empty
# volume, and can be re-run on an existing volume with: docker compose exec -T postgres sh /docker-entrypoint-initdb.d/016_application_role_password.sh
# Both values reach psql as variables (:"role" quotes an identifier, :'pw' a literal), never by interpolation into shell or SQL text,
# and the password is not echoed or placed on a command line.
set -eu
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set to initialise the application role}"
psql -v ON_ERROR_STOP=1 -v pw="$APP_DB_PASSWORD" -v role="${APP_DB_USER:-zm_terminal_app}" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
ALTER ROLE :"role" PASSWORD :'pw';
SQL
