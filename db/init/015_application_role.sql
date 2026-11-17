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
-- The role name comes from the zm.app_role setting (default zm_terminal_app). compose.yml sets it for every psql in the Postgres
-- container from APP_DB_USER through PGOPTIONS, so the role, its password (016) and the API all use the same APP_DB_* values.
-- Tests override it with SET zm.app_role = '<disposable role>' so they never touch the real application role.
-- CONVERGENCE: this script does not only add grants, it establishes the whole boundary, so an existing or previously over-privileged
-- role is corrected: every role membership is revoked (an inherited or SET ROLE-reachable admin role would defeat all grants), role
-- attributes (including LOGIN) are re-established, every stored role setting (global and for this database) is cleared, CREATE on
-- the schema and database is revoked, and it fails closed if the role still
-- has CREATE on the schema (for example through a PUBLIC grant on a pre-PG15 public schema) or owns anything.

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
    -- Establish the whole identity explicitly instead of assuming an existing role's attributes (an old role may be NOLOGIN or SUPERUSER).
    EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT -1', app_role);
    -- Stored role settings: PostgreSQL keeps them per (role, database) in pg_db_role_setting, and a role applies the global entry and the
    -- one for the database it logs into. RESET ALL clears every stored GUC of each entry (no blacklist), so something like
    -- ALTER ROLE ... IN DATABASE ... SET session_replication_role = replica cannot survive and silently disable the triggers at login.
    -- Entries for the same role in OTHER databases, and every other role's settings, are deliberately left alone.
    EXECUTE format('ALTER ROLE %I RESET ALL', app_role);
    EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL', app_role, current_database());
    IF EXISTS (SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
               WHERE r.rolname = app_role AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))) THEN
        RAISE EXCEPTION 'application role % still has stored role settings for database % after RESET ALL', app_role, current_database();
    END IF;

    -- Revoke EVERY membership of the application role, whoever granted it (PG16+ tracks one row per grantor). Revoking the direct
    -- membership also removes indirect paths (app -> intermediate -> admin). The application needs no inherited role.
    FOR rel IN SELECT r.rolname AS granted, g.rolname AS grantor
               FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.grantor
               WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = app_role) LOOP
        EXECUTE format('REVOKE %I FROM %I GRANTED BY %I', rel.granted, app_role, rel.grantor);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = app_role)) THEN
        RAISE EXCEPTION 'application role % still belongs to another role after revocation', app_role;
    END IF;

    -- Refuse to continue if the application role owns anything here: ownership would defeat every grant below.
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = sch AND pg_get_userbyid(c.relowner) = app_role)
       OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = sch AND pg_get_userbyid(p.proowner) = app_role)
       OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname = sch AND pg_get_userbyid(n.nspowner) = app_role)
       OR EXISTS (SELECT 1 FROM pg_database d WHERE d.datname = current_database() AND pg_get_userbyid(d.datdba) = app_role) THEN
        RAISE EXCEPTION 'application role % owns the database, the schema % or objects in it; ownership must stay with the admin role', app_role, sch;
    END IF;

    -- Schema: USAGE only. CREATE would let the role add or shadow objects. TEMP on the database is left as PostgreSQL's default.
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', sch, app_role);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', sch, app_role);
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM %I', current_database(), app_role);
    IF has_schema_privilege(app_role, sch, 'CREATE') THEN  -- only reachable through PUBLIC (pre-PG15 public schema): do not guess
        RAISE EXCEPTION 'application role % can still CREATE in schema % (granted to PUBLIC?): run REVOKE CREATE ON SCHEMA % FROM PUBLIC', app_role, sch, sch;
    END IF;
    IF has_database_privilege(app_role, current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'application role % can still CREATE schemas in database %', app_role, current_database();
    END IF;

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
