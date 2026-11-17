#!/bin/sh
# Sets the application role's password from APP_DB_PASSWORD on first database initialisation (empty volume only).
# Runs after 015_application_role.sql. The value is passed as a psql variable so it is never interpolated into shell or SQL text.
set -eu
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set to initialise the application role}"
psql -v ON_ERROR_STOP=1 -v pw="$APP_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
ALTER ROLE zm_terminal_app PASSWORD :'pw';
SQL
