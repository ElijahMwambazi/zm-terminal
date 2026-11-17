-- Application (API) database role, separate from the admin/owner role that runs migrations.
-- The admin role (POSTGRES_USER, e.g. zm_terminal) owns every table, view, sequence and function and is the only role that may run DDL,
-- TRUNCATE or DROP. The API connects as zm_terminal_app, which owns nothing and holds only the DML the application needs, so
-- compromised or buggy application credentials cannot truncate/drop/alter history or regain destructive rights through ownership.
-- TRUNCATE is its own privilege (not covered by DELETE), so it is simply never granted. Event triggers are not needed.
--
-- Grants (all in the schema this script runs in, normally public):
--   * legacy tables (migrations 001-013): SELECT, INSERT, UPDATE  (the stores use INSERT ... ON CONFLICT DO UPDATE; no DELETE anywhere)
--   * Energy tables (migration 014, when present): SELECT and INSERT; UPDATE only on the lifecycle-status columns the importer will
--     need (candidate review fields, publication/import status). No DELETE, TRUNCATE, REFERENCES or TRIGGER, no DDL.
--   * views: SELECT. sequences: USAGE, SELECT. Never ownership. No password here: db/init/016_application_role_password.sh sets it.
-- The role is created with LOGIN but no password, so it cannot authenticate over password auth until one is set.
-- Idempotent. Re-run it after applying a later migration (e.g. 014 on an existing database) so the new objects are granted:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal -v ON_ERROR_STOP=1 --single-transaction < db/init/015_application_role.sql
-- Tests override the role name with SET zm.app_role = '<disposable role>' so they never touch the real application role.

DO $$
DECLARE
    app_role text := COALESCE(NULLIF(current_setting('zm.app_role', true), ''), 'zm_terminal_app');
    sch text := current_schema();
    rel record;
BEGIN
    IF app_role = current_user THEN
        RAISE EXCEPTION 'the application role must differ from the admin role running this script (%)', app_role;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        EXECUTE format('CREATE ROLE %I LOGIN', app_role);
    END IF;
    EXECUTE format('ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT', app_role);

    -- Refuse to continue if the application role owns anything here: ownership would defeat every grant below.
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = sch AND pg_get_userbyid(c.relowner) = app_role)
       OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = sch AND pg_get_userbyid(p.proowner) = app_role)
       OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname = sch AND pg_get_userbyid(n.nspowner) = app_role) THEN
        RAISE EXCEPTION 'application role % owns objects in schema %; ownership must stay with the admin role', app_role, sch;
    END IF;

    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', sch, app_role);

    FOR rel IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = sch AND c.relkind IN ('r', 'p', 'v', 'S') LOOP
        -- Start from nothing so a re-run also removes any grant that is no longer intended.
        IF rel.relkind = 'S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %I.%I FROM %I', sch, rel.relname, app_role);
            EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.%I TO %I', sch, rel.relname, app_role);
        ELSE
            EXECUTE format('REVOKE ALL ON %I.%I FROM %I', sch, rel.relname, app_role);
            IF rel.relkind = 'v' THEN
                EXECUTE format('GRANT SELECT ON %I.%I TO %I', sch, rel.relname, app_role);
            ELSIF rel.relname LIKE 'energy\_%' THEN
                EXECUTE format('GRANT SELECT, INSERT ON %I.%I TO %I', sch, rel.relname, app_role);
            ELSE
                EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I.%I TO %I', sch, rel.relname, app_role);
            END IF;
        END IF;
    END LOOP;

    -- Energy: UPDATE is limited to lifecycle-status columns (the BEFORE UPDATE triggers still enforce the legal transitions).
    IF to_regclass(format('%I.energy_publication_candidates', sch)) IS NOT NULL THEN
        EXECUTE format('GRANT UPDATE (review_status, review_note, reviewed_at, accepted_publication_id) ON %I.energy_publication_candidates TO %I', sch, app_role);
    END IF;
    IF to_regclass(format('%I.energy_publications', sch)) IS NOT NULL THEN
        EXECUTE format('GRANT UPDATE (publication_status, status_changed_at, status_note) ON %I.energy_publications TO %I', sch, app_role);
    END IF;
    IF to_regclass(format('%I.energy_generation_imports', sch)) IS NOT NULL THEN
        EXECUTE format('GRANT UPDATE (import_status, status_changed_at, status_note) ON %I.energy_generation_imports TO %I', sch, app_role);
    END IF;
END
$$;
