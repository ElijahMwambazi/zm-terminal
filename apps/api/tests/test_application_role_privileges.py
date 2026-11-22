"""Migration 015: the API's application role versus the admin/owner role. PostgreSQL tests in a disposable database; SYNTHETIC data.

Everything runs in a throwaway schema (001-013 + 014 + 015) with a throwaway, randomly named application role created by the test
and dropped afterwards (SET zm.app_role), so neither the normal database nor the real `zm_terminal_app` role is ever touched.
The application engine logs in as that role over a real connection; a superuser `SET ROLE` would not prove the same thing.
"""

import secrets
import sys
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from disposable_db import reset_public
from test_energy_vintage_schema import (NOW, SERIES, add_import, add_obs, add_total, candidate, link_candidate, one, publish, sha,
                                        station, supersede_import)
from app.schemas.fx import FxQuote
from app.services import fx_store

INIT = Path(__file__).parents[3] / "db/init"
ENERGY = ("energy_generation_group_totals", "energy_generation_observations", "energy_generation_imports",
          "energy_publication_candidates", "energy_publications", "energy_source_stations")
ENERGY_VIEWS = ("energy_publication_current_imports", "energy_generation_canonical")
BANK_FX = ("bank_fx_snapshots", "bank_fx_rates")
BANK_FX_VIEWS = ("bank_fx_canonical_snapshots",)
INSUFFICIENT_PRIVILEGE = "42501"


class Env:
    def __init__(self, schema, admin, app, role, admin_user, mid=None, other=None):
        self.schema, self.admin, self.app, self.role, self.admin_user, self.mid, self.other = schema, admin, app, role, admin_user, mid, other


DEPENDENT_OBJECTS_STILL_EXIST = "2BP01"  # SQLSTATE of DROP ROLE while objects (e.g. TEMP tables of a backend still exiting) still depend on it
DROP_ROLE_ATTEMPTS = 50  # at most 50 attempts x 0.1 s = 5 s of sleeping, then the original error is raised


def drop_role(admin, r):
    """End the role's sessions (only those of this uniquely named test role), then drop it. pg_terminate_backend only signals: a TEMP table
    of a backend that is still exiting can make DROP ROLE fail with dependent_objects_still_exist (2BP01), so that one condition, and
    nothing else, is retried, up to DROP_ROLE_ATTEMPTS times. Any other error (privileges, syntax, connection, ...) is raised at once, and
    when the retries run out the last 2BP01 error is raised unchanged. The race is documented PostgreSQL behaviour but could not be
    reproduced here (no retry was needed in repeated runs), so the connections the tests own are still closed first (app.dispose())."""
    for attempt in range(1, DROP_ROLE_ATTEMPTS + 1):
        try:
            with admin.begin() as c:
                if not one(c, "SELECT COUNT(*) FROM pg_roles WHERE rolname=:r", r=r):
                    return
                c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=:r"), {"r": r})
                c.execute(text(f"DROP OWNED BY {r}"))
                c.execute(text(f"DROP ROLE {r}"))
            return
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != DEPENDENT_OBJECTS_STILL_EXIST or attempt == DROP_ROLE_ATTEMPTS:
                raise
            time.sleep(0.1)


class _SqlStateError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _FakeAdmin:
    """Stands in for the admin engine: the role exists and DROP ROLE raises the scripted SQLSTATEs in turn (None = success)."""
    def __init__(self, script):
        self.script, self.drop_attempts = list(script), 0

    @contextmanager
    def begin(self):
        outer = self

        class Connection:
            def execute(self, statement, params=None):
                if str(statement).startswith("DROP ROLE"):
                    outer.drop_attempts += 1
                    state = outer.script.pop(0) if outer.script else None
                    if state:
                        raise DBAPIError("DROP ROLE", None, _SqlStateError(state))
        yield Connection()


def _drop_with(monkeypatch, script):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    admin = _FakeAdmin(script)
    return admin, sleeps


def test_drop_role_retries_only_dependent_objects_still_exist_and_is_bounded(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "one", lambda c, sql, **p: 1)  # the role "exists"
    admin, sleeps = _drop_with(monkeypatch, [DEPENDENT_OBJECTS_STILL_EXIST, DEPENDENT_OBJECTS_STILL_EXIST, None])
    drop_role(admin, "zm_t_x")  # transient dependency condition: retried until it succeeds
    assert admin.drop_attempts == 3 and sleeps == [0.1, 0.1]
    for state in ("42501", "42601", "08006", "55006"):  # privilege, syntax, connection, object_in_use: never retried
        admin, sleeps = _drop_with(monkeypatch, [state])
        with pytest.raises(DBAPIError) as raised:
            drop_role(admin, "zm_t_x")
        assert raised.value.orig.sqlstate == state and admin.drop_attempts == 1 and sleeps == []
    admin, sleeps = _drop_with(monkeypatch, [DEPENDENT_OBJECTS_STILL_EXIST] * 1000)  # never clears: bounded, original error raised
    with pytest.raises(DBAPIError) as raised:
        drop_role(admin, "zm_t_x")
    assert raised.value.orig.sqlstate == DEPENDENT_OBJECTS_STILL_EXIST
    assert admin.drop_attempts == DROP_ROLE_ATTEMPTS and len(sleeps) == DROP_ROLE_ATTEMPTS - 1


@contextmanager
def make_env(database, prepare=None):
    """A reset `public` schema with 001-014, then `prepare(admin_connection, info)` (to pre-create an unsafe role), then 015, then a real
    login engine for the application role. Installed in `public` as deployed. Afterwards the throwaway roles and everything granted to
    them in the database are removed (the database itself is dropped at the end of the session)."""
    url, admin = database
    role = "zm_t_app_" + secrets.token_hex(4)
    mid = "zm_t_mid_" + secrets.token_hex(4)
    other = "zm_t_other_" + secrets.token_hex(4)
    # quote, apostrophe, space, #, /, :, @, $, backslash: the engine URL is built with URL.set(), so nothing is escaped by hand
    password = secrets.token_hex(8) + ' "q\' #/:@$\\x'
    reset_public(admin)
    try:
        with admin.begin() as c:
            admin_user = one(c, "SELECT current_user")
            for migration in sorted(INIT.glob("0[01][0-9]_*.sql")):
                if migration.name.startswith("015"):
                    if prepare:
                        prepare(c, {"schema": "public", "role": role, "mid": mid, "other": other, "admin_user": admin_user,
                                    "db": one(c, "SELECT current_database()")})
                    c.execute(text(f"SET LOCAL zm.app_role = '{role}'"))
                c.execute(text(migration.read_text()))
            # what 016 does in production, with the value quoted by the driver (never string-formatted into SQL)
            c.connection.driver_connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        app = create_engine(url.set(username=role, password=password))
        try:
            yield Env("public", admin, app, role, admin_user, mid if prepare else None, other)
        finally:
            app.dispose()
    finally:
        reset_public(admin)  # removes the objects the roles own/see; then their database-level grants and settings, then the roles
        for r in (role, mid, other):
            drop_role(admin, r)


@pytest.fixture
def env(disposable_database):
    with make_env(disposable_database) as e:
        yield e


def make_unsafe(c, i):
    """A role as an earlier, over-privileged deployment might have left it: superuser-ish attributes, direct and indirect membership
    of the admin role, CREATE on the schema, TRUNCATE/DELETE on Energy tables and a hijacking search_path."""
    c.execute(text(f"CREATE ROLE {i['role']} NOLOGIN SUPERUSER CREATEDB CREATEROLE REPLICATION BYPASSRLS INHERIT"))
    c.execute(text(f"CREATE ROLE {i['mid']} NOLOGIN INHERIT"))
    c.execute(text(f"GRANT {i['admin_user']} TO {i['mid']}"))  # indirect: role -> mid -> admin
    c.execute(text(f"GRANT {i['mid']} TO {i['role']}"))
    c.execute(text(f"GRANT {i['admin_user']} TO {i['role']}"))  # direct membership of the admin role
    c.execute(text(f'GRANT CREATE ON SCHEMA "{i["schema"]}" TO {i["role"]}'))
    c.execute(text(f"GRANT CREATE ON DATABASE {one(c, 'SELECT current_database()')} TO {i['role']}"))
    c.execute(text(f"ALTER ROLE {i['role']} SET search_path = pg_temp"))  # global role setting
    set_database_settings(c, i)
    for table in ENERGY:
        c.execute(text(f'GRANT ALL ON "{i["schema"]}".{table} TO {i["role"]}'))
    # the starting point really is unsafe (so the convergence test proves a change, not a no-op)
    assert not one(c, "SELECT rolcanlogin FROM pg_roles WHERE rolname=:r", r=i["role"])
    assert stored_settings(c, i["role"], i["db"]) >= {"search_path=pg_temp", "session_replication_role=replica", "application_name=hostile_setting"}
    assert one(c, "SELECT rolsuper AND rolcreaterole AND rolcreatedb FROM pg_roles WHERE rolname=:r", r=i["role"])
    assert one(c, "SELECT pg_has_role(:r, :a, 'MEMBER') AND pg_has_role(:r, :a, 'USAGE')", r=i["role"], a=i["admin_user"])
    assert one(c, "SELECT pg_has_role(:r, :m, 'MEMBER')", r=i["role"], m=i["mid"])
    assert one(c, "SELECT has_schema_privilege(:r, :s, 'CREATE')", r=i["role"], s=i["schema"])
    assert one(c, "SELECT has_table_privilege(:r, :t, 'TRUNCATE')", r=i["role"], t=f'{i["schema"]}.energy_generation_imports')


OTHER_ROLE_SETTINGS = {"application_name=other_role_setting", "statement_timeout=12345"}


def make_nologin(c, i):
    c.execute(text(f"CREATE ROLE {i['role']} NOLOGIN"))
    assert not one(c, "SELECT rolcanlogin FROM pg_roles WHERE rolname=:r", r=i["role"])


def set_database_settings(c, i):
    """Settings stored for (role, THIS database): not cleared by a plain ALTER ROLE ... RESET ALL. session_replication_role = replica
    would disable every ordinary trigger from the first statement of each login."""
    c.execute(text(f"ALTER ROLE {i['role']} IN DATABASE {i['db']} SET session_replication_role = replica"))
    c.execute(text(f"ALTER ROLE {i['role']} IN DATABASE {i['db']} SET application_name = 'hostile_setting'"))  # a second, arbitrary GUC


def stored_settings(c, role, db):
    """Every stored setting that applies to (role, database): the role's global entry and its entry for this database."""
    rows = c.execute(text("SELECT unnest(s.setconfig) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole "
                          "WHERE r.rolname=:r AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname=:d))"), {"r": role, "d": db}).all()
    return {r[0] for r in rows}


def make_login_less_and_set(c, i):
    """A role as above but otherwise tame: only NOLOGIN, stored settings, and an unrelated role whose settings must survive."""
    c.execute(text(f"CREATE ROLE {i['role']} NOLOGIN"))
    set_database_settings(c, i)
    c.execute(text(f"CREATE ROLE {i['other']} NOLOGIN"))
    c.execute(text(f"ALTER ROLE {i['other']} IN DATABASE {i['db']} SET application_name = 'other_role_setting'"))
    c.execute(text(f"ALTER ROLE {i['other']} SET statement_timeout = '12345'"))
    assert stored_settings(c, i["other"], i["db"]) == OTHER_ROLE_SETTINGS


@pytest.fixture
def unsafe_env(disposable_database):
    with make_env(disposable_database, make_unsafe) as e:
        yield e


def denied(app, sql, **params):
    with pytest.raises(ProgrammingError) as err:
        with app.begin() as c:
            c.execute(text(sql), params)
    assert err.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE, err.value.orig


def seed_energy(env):
    with env.admin.begin() as c:
        st = station(c, "S")
        pub, imp = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    return st, pub, imp


def energy_state(env):
    with env.admin.connect() as c:
        return [one(c, f"SELECT COUNT(*) FROM {t}") for t in ENERGY]


# ------------------------------------------------------------------------------------------------------------ roles and ownership
def test_application_role_is_unprivileged_and_owns_nothing(env):
    with env.admin.connect() as c:
        attrs = c.execute(text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin "
                               "FROM pg_roles WHERE rolname=:r"), {"r": env.role}).one()
        assert attrs == (False, False, False, False, False, True)
        owned = c.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                               "WHERE n.nspname=:s AND pg_get_userbyid(c.relowner)=:r"), {"s": env.schema, "r": env.role}).all()
        funcs = c.execute(text("SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                               "WHERE n.nspname=:s AND pg_get_userbyid(p.proowner)=:r"), {"s": env.schema, "r": env.role}).all()
        assert owned == [] and funcs == []
        assert one(c, "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=:s", s=env.schema) != env.role


def test_admin_role_owns_every_energy_object_and_function(env):
    with env.admin.connect() as c:
        rel_owners = {r[0]: r[1] for r in c.execute(text(
            "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:s AND c.relname LIKE 'energy\\_%'"), {"s": env.schema})}
        fn_owners = {r[0]: r[1] for r in c.execute(text(
            "SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=:s AND p.proname LIKE 'energy\\_%'"), {"s": env.schema})}
    assert set(ENERGY + ENERGY_VIEWS) <= set(rel_owners) and fn_owners
    assert set(rel_owners.values()) == {env.admin_user} and set(fn_owners.values()) == {env.admin_user}


def test_energy_privilege_matrix(env):
    def has(priv, table):
        with env.admin.connect() as c:
            return one(c, "SELECT has_table_privilege(:r, :t, :p)", r=env.role, t=f"{env.schema}.{table}", p=priv)

    def col(table, column):
        with env.admin.connect() as c:
            return one(c, "SELECT has_column_privilege(:r, :t, :col, 'UPDATE')", r=env.role, t=f"{env.schema}.{table}", col=column)
    for table in ENERGY:
        assert has("SELECT", table) and has("INSERT", table), table
        for forbidden in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "UPDATE"):  # table-level UPDATE is never granted
            assert not has(forbidden, table), (table, forbidden)
    for view in ENERGY_VIEWS:
        assert has("SELECT", view) and not any(has(p, view) for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
    # UPDATE only on lifecycle-status columns
    assert all(col("energy_publications", c) for c in ("publication_status", "status_changed_at", "status_note"))
    assert all(col("energy_generation_imports", c) for c in ("import_status", "status_changed_at", "status_note"))
    assert all(col("energy_publication_candidates", c) for c in ("review_status", "review_note", "reviewed_at", "accepted_publication_id"))
    for table, column in (("energy_publications", "title"), ("energy_publications", "source_sha256"), ("energy_generation_imports", "expected_observation_count"),
                          ("energy_generation_observations", "generation_gwh"), ("energy_generation_group_totals", "total_gwh"),
                          ("energy_source_stations", "station_name"), ("energy_publication_candidates", "source_sha256")):
        assert not col(table, column), (table, column)


# ------------------------------------------------------------------------------------------------------------ destructive denial
def test_application_role_cannot_truncate_drop_or_alter_energy_objects(env):
    seed_energy(env)
    before = energy_state(env)
    for table in ("energy_generation_observations", "energy_generation_imports"):
        denied(env.app, f"TRUNCATE {table}")
        denied(env.app, f"TRUNCATE {table} CASCADE")
        denied(env.app, f"DROP TABLE {table}")
        denied(env.app, f"DROP TABLE {table} CASCADE")
        denied(env.app, f"ALTER TABLE {table} ADD COLUMN sneaky INTEGER")
        denied(env.app, f"ALTER TABLE {table} DISABLE TRIGGER ALL")
        denied(env.app, f"ALTER TABLE {table} OWNER TO {env.role}")
        with env.app.begin() as c:  # PostgreSQL only warns that nothing was granted (no grant option); the effect is what matters
            c.execute(text(f"GRANT ALL ON {table} TO {env.role}"))
        with env.admin.connect() as c:
            assert not one(c, "SELECT has_table_privilege(:r, :t, 'TRUNCATE')", r=env.role, t=f"{env.schema}.{table}")
    for table in ENERGY:
        denied(env.app, f"TRUNCATE {table}")
    denied(env.app, "DROP TRIGGER energy_generation_observations_immutable ON energy_generation_observations")
    denied(env.app, "DROP VIEW energy_generation_canonical")
    denied(env.app, "CREATE OR REPLACE FUNCTION energy_reject_change() RETURNS trigger AS $$ BEGIN RETURN NEW; END; $$ LANGUAGE plpgsql")
    denied(env.app, "DROP FUNCTION energy_reject_delete() CASCADE")
    denied(env.app, "CREATE TABLE app_made_table (id INTEGER)")  # no CREATE on the schema either
    assert energy_state(env) == before and all(n >= 1 for n in before)
    with env.admin.connect() as c:  # nothing changed: every object, trigger and the row data are intact
        assert one(c, "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='sneaky'") == 0
        assert one(c, "SELECT COUNT(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relname='energy_generation_observations' AND NOT t.tgisinternal") == 4
        assert one(c, "SELECT COUNT(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relname='energy_generation_observations' AND t.tgenabled='O' AND NOT t.tgisinternal") == 4


def test_application_role_cannot_bypass_immutability_through_delete_update_or_replica_mode(env):
    st, pub, imp = seed_energy(env)
    denied(env.app, "DELETE FROM energy_generation_observations")
    denied(env.app, "DELETE FROM energy_publications")
    denied(env.app, "UPDATE energy_generation_observations SET generation_gwh=9")
    denied(env.app, "UPDATE energy_generation_group_totals SET total_gwh=9")
    denied(env.app, "UPDATE energy_publications SET title='x'")
    denied(env.app, "UPDATE energy_source_stations SET station_name='x'")
    denied(env.app, "SET session_replication_role = replica")  # would disable the triggers if it were allowed
    # The narrow UPDATE grant is still gated by the transition triggers: a legal column, an illegal transition.
    with pytest.raises(IntegrityError):
        with env.app.begin() as c:
            c.execute(text("UPDATE energy_publications SET publication_status='rejected' WHERE id=:i"), {"i": pub})
    with pytest.raises(IntegrityError):
        with env.app.begin() as c:
            c.execute(text("UPDATE energy_generation_imports SET import_status='current', status_note='n' WHERE id=:i"), {"i": imp})
    with env.admin.connect() as c:
        assert one(c, "SELECT generation_gwh FROM energy_generation_observations") == Decimal("1.00")
        assert one(c, "SELECT publication_status FROM energy_publications WHERE id=:i", i=pub) == "approved"


# ------------------------------------------------------------------------------------------------------- what the importer needs
def run_importer_lifecycle(app):
    with app.begin() as c:  # first import: stations, candidate, publication, import, rows, candidate acceptance (SELECT/INSERT/UPDATE of status)
        st = station(c, "S")
        cand = candidate(c, 1)
        pub = one(c, "INSERT INTO energy_publications (publisher, source_series, publication_year, revision_sequence, publication_status, "
                     "title, source_url, source_sha256, source_size_bytes, pdf_pages, rights_status, first_retrieved_at) "
                     "VALUES ('ERB',:s,2025,1,'approved','Synthetic bulletin',:u,:h,1000,10,'review_required',:t) RETURNING id",
                  s=SERIES, u="https://example.invalid/1.pdf", h=sha(1), t=NOW)
        imp = add_import(c, pub, 1, 1, 1)
        add_obs(c, imp, st, 2020, "1.00")
        add_total(c, imp, 2020, "100.00")
        link_candidate(c, cand, pub)
    with app.begin() as c:  # local correction swap: new import, demote the old one
        i2 = add_import(c, pub, 2, 1, 0, reason="parser fix")
        add_obs(c, i2, st, 2020, "1.25")
        supersede_import(c, imp)
    with app.begin() as c:  # same-year replacement r2 supersedes r1
        publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=pub)
    with app.connect() as c:
        assert one(c, "SELECT generation_gwh FROM energy_generation_canonical") == Decimal("2.00")
        assert one(c, "SELECT COUNT(*) FROM energy_generation_imports") == 3


def test_application_role_can_run_the_full_importer_lifecycle(env):
    run_importer_lifecycle(env.app)


def test_015_is_idempotent_and_refuses_unsafe_configurations(env):
    with env.admin.connect() as c:
        grants = lambda: c.execute(text("SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
                                        "WHERE grantee=:r AND table_schema=:s ORDER BY 1,2,3"), {"r": env.role, "s": env.schema}).all()
        before = grants()
    with env.admin.begin() as c:
        c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
        c.execute(text((INIT / "015_application_role.sql").read_text()))
    with env.admin.connect() as c:
        after = c.execute(text("SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
                               "WHERE grantee=:r AND table_schema=:s ORDER BY 1,2,3"), {"r": env.role, "s": env.schema}).all()
    assert before == after and before
    with pytest.raises(DBAPIError, match="must differ from the admin role"):
        with env.admin.begin() as c:
            c.execute(text(f"SET LOCAL zm.app_role = '{env.admin_user}'"))
            c.execute(text((INIT / "015_application_role.sql").read_text()))
    with env.admin.begin() as c:  # an application role that owns an object would defeat every grant: 015 refuses
        c.execute(text(f"CREATE TABLE owned_by_app (id INTEGER)"))
        c.execute(text(f"ALTER TABLE owned_by_app OWNER TO {env.role}"))
    with pytest.raises(DBAPIError, match="owns the database, the schema"):
        with env.admin.begin() as c:
            c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
            c.execute(text((INIT / "015_application_role.sql").read_text()))


# ---------------------------------------------------------------------------------------------------------------------- convergence
def assert_app_role_is_converged(e):
    with e.admin.connect() as c:
        assert c.execute(text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit, rolcanlogin, rolconnlimit "
                              "FROM pg_roles WHERE rolname=:r"), {"r": e.role}).one() == (False, False, False, False, False, False, True, -1)
        assert one(c, "SELECT COUNT(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=:r", r=e.role) == 0
        assert one(c, "SELECT COUNT(*) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole WHERE r.rolname=:r", r=e.role) == 0
        assert stored_settings(c, e.role, one(c, "SELECT current_database()")) == set()  # global AND this database
        for target in (e.admin_user, e.mid):
            if target:
                assert not one(c, "SELECT pg_has_role(:r, :t, 'MEMBER')", r=e.role, t=target)
        assert not one(c, "SELECT has_schema_privilege(:r, :s, 'CREATE')", r=e.role, s=e.schema)
        assert one(c, "SELECT has_schema_privilege(:r, :s, 'USAGE')", r=e.role, s=e.schema)
        assert not one(c, "SELECT has_database_privilege(:r, current_database(), 'CREATE')", r=e.role)
        for table in ENERGY:
            for priv in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "UPDATE"):
                assert not one(c, "SELECT has_table_privilege(:r, :t, :p)", r=e.role, t=f"{e.schema}.{table}", p=priv), (table, priv)


def assert_application_cannot_escalate_or_create(e):
    denied(e.app, f"SET ROLE {e.admin_user}")  # no membership left, direct or indirect
    if e.mid:
        denied(e.app, f"SET ROLE {e.mid}")
    denied(e.app, "CREATE TABLE app_made_table (id INTEGER)")
    denied(e.app, "CREATE VIEW app_made_view AS SELECT 1 AS x")
    denied(e.app, "CREATE FUNCTION app_made_function() RETURNS integer AS 'SELECT 1' LANGUAGE sql")
    denied(e.app, "CREATE OR REPLACE FUNCTION energy_reject_delete() RETURNS trigger AS $$ BEGIN RETURN OLD; END; $$ LANGUAGE plpgsql")
    denied(e.app, "CREATE SCHEMA app_made_schema")
    denied(e.app, "CREATE ROLE app_made_role")
    for table in ("energy_generation_observations", "energy_generation_imports"):
        denied(e.app, f"TRUNCATE {table}")
        denied(e.app, f"DROP TABLE {table}")
        denied(e.app, f"ALTER TABLE {table} ADD COLUMN sneaky INTEGER")
        denied(e.app, f"DELETE FROM {table}")


def assert_fresh_login_is_safe(e):
    """A NEW real connection as the application role (the pool is discarded first): no stored setting reaches the session, and the
    ordinary Energy triggers still fire for it. With session_replication_role = replica the illegal update below would succeed."""
    e.app.dispose()
    with e.app.connect() as c:
        assert one(c, "SELECT current_user") == e.role and one(c, "SELECT session_user") == e.role
        assert c.execute(text("SHOW session_replication_role")).scalar() == "origin"
        assert c.execute(text("SHOW application_name")).scalar() != "hostile_setting"
        assert c.execute(text("SHOW statement_timeout")).scalar() == "0"
        assert not one(c, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
    run_importer_lifecycle(e.app)  # leaves a superseded import behind
    with pytest.raises(IntegrityError, match="illegal transition"):  # the BEFORE UPDATE trigger, not a CHECK, rejects this
        with e.app.begin() as c:
            c.execute(text("UPDATE energy_generation_imports SET import_status='current', status_note='n' "
                           "WHERE id=(SELECT MIN(id) FROM energy_generation_imports WHERE import_status='superseded')"))


def test_nologin_role_converges_to_login_and_a_fresh_tcp_login_works(disposable_database):
    with make_env(disposable_database, make_nologin) as e:  # created NOLOGIN; 015 + the password step must make it a usable application identity
        with e.admin.connect() as c:
            assert one(c, "SELECT rolcanlogin FROM pg_roles WHERE rolname=:r", r=e.role) is True
            db = one(c, "SELECT current_database()")
        e.app.dispose()
        with e.app.connect() as c:  # real authentication with the special-character password
            assert (one(c, "SELECT current_user"), one(c, "SELECT current_database()")) == (e.role, db)
            assert one(c, "SELECT COUNT(*) >= 0 FROM fx_rates") is True
            assert not one(c, "SELECT has_table_privilege(current_user, 'fx_rates', 'TRUNCATE')")
        denied(e.app, "TRUNCATE fx_rates")
        denied(e.app, "TRUNCATE energy_generation_observations")


def test_database_specific_role_settings_are_cleared_and_a_fresh_login_is_safe(disposable_database):
    with make_env(disposable_database, make_login_less_and_set) as e:
        with e.admin.connect() as c:
            db = one(c, "SELECT current_database()")
            assert stored_settings(c, e.role, db) == set()  # the app role's global and per-database settings are gone
            assert stored_settings(c, e.other, db) == OTHER_ROLE_SETTINGS  # another role's entries (global and per-database) are untouched
            assert one(c, "SELECT COUNT(*) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole WHERE r.rolname=:r", r=e.other) == 2
        assert_fresh_login_is_safe(e)


def test_application_role_can_never_create_or_replace_objects_in_the_schema(env):
    assert_app_role_is_converged(env)
    assert_application_cannot_escalate_or_create(env)


def test_015_converges_an_existing_over_privileged_role(unsafe_env):
    e = unsafe_env  # created NOLOGIN and SUPERUSER, with global + per-database stored settings (session_replication_role = replica), member of the admin role (directly and through an intermediate role), CREATE on schema/db, TRUNCATE on Energy
    assert_app_role_is_converged(e)  # attributes, memberships, role settings, schema/database CREATE and direct Energy grants all reset
    assert_application_cannot_escalate_or_create(e)  # SET ROLE admin / intermediate refused, no CREATE, no TRUNCATE/DROP/ALTER/DELETE
    assert_fresh_login_is_safe(e)  # a new login as the (formerly NOLOGIN) role: origin replication mode, no stored settings, triggers fire
    # (assert_fresh_login_is_safe also ran the intended SELECT / INSERT / status-update lifecycle as the application role)


def test_015_fails_closed_when_public_can_create_in_the_schema(env):
    with env.admin.begin() as c:
        c.execute(text(f'GRANT CREATE ON SCHEMA "{env.schema}" TO PUBLIC'))  # e.g. a pre-PG15 public schema
    try:
        with pytest.raises(DBAPIError, match="can still CREATE in schema"):
            with env.admin.begin() as c:
                c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
                c.execute(text((INIT / "015_application_role.sql").read_text()))
    finally:
        with env.admin.begin() as c:
            c.execute(text(f'REVOKE CREATE ON SCHEMA "{env.schema}" FROM PUBLIC'))
    with env.admin.begin() as c:  # once PUBLIC no longer has it, 015 converges
        c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
        c.execute(text((INIT / "015_application_role.sql").read_text()))


# ------------------------------------------------------------------------------------------------- existing application behaviour
def test_existing_fx_application_behaviour_works_as_the_application_role(env):
    quote = FxQuote(buy="19.8736", mid="19.8987", sell="19.9236", observed_at=NOW, retrieved_at=NOW, stale=False)
    assert fx_store.save_fx_quote(quote, engine=env.app) is True  # INSERT ... ON CONFLICT DO NOTHING, BIGSERIAL sequence
    assert fx_store.save_fx_quote(quote, engine=env.app) is False
    points = fx_store.fetch_fx_history("ALL", now=NOW, engine=env.app)
    assert [str(p.mid) for p in points] == ["19.8987"]
    assert fx_store.fetch_latest_fx_mid(engine=env.app) is not None


def test_application_role_has_the_dml_the_stores_use_on_every_legacy_table_and_no_destructive_privileges(env):
    with env.admin.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename NOT LIKE 'energy\\_%' AND tablename NOT LIKE 'bank\\_fx\\_%'"))]
        assert len(tables) >= 14
        for t in tables:
            for priv, expected in (("SELECT", True), ("INSERT", True), ("UPDATE", True), ("DELETE", False), ("TRUNCATE", False),
                                   ("REFERENCES", False), ("TRIGGER", False)):
                assert one(c, "SELECT has_table_privilege(:r, :t, :p)", r=env.role, t=f"{env.schema}.{t}", p=priv) is expected, (t, priv)
    for t in tables:
        with env.app.connect() as c:  # a real SELECT on each table, plus rejected TRUNCATE on a legacy table
            c.execute(text(f"SELECT 1 FROM {t} LIMIT 1"))
    denied(env.app, "TRUNCATE fx_rates")
    denied(env.app, "DROP TABLE fx_rates")


# ------------------------------------------------------------------------------------------------------------------ Bank FX (017)
def bank_fx_grants(env):
    with env.admin.connect() as c:
        return c.execute(text("SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee=:r AND table_schema=:s "
                              "AND table_name LIKE 'bank\\_fx\\_%' ORDER BY 1,2"), {"r": env.role, "s": env.schema}).all()


def bank_fx_sequence_privileges(env):
    with env.admin.connect() as c:
        return {(seq, priv): one(c, "SELECT has_sequence_privilege(:r, :s, :p)", r=env.role, s=f"{env.schema}.{seq}", p=priv)
                for seq in ("bank_fx_snapshots_id_seq", "bank_fx_rates_id_seq") for priv in ("USAGE", "SELECT", "UPDATE")}


def test_bank_fx_privilege_matrix_on_a_fresh_install(env):  # 015 ran BEFORE 017 here: 017 granted its own new objects
    def has(priv, table):
        with env.admin.connect() as c:
            return one(c, "SELECT has_table_privilege(:r, :t, :p)", r=env.role, t=f"{env.schema}.{table}", p=priv)
    for table in BANK_FX:
        assert has("SELECT", table) and has("INSERT", table), table
        for forbidden in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not has(forbidden, table), (table, forbidden)
    for view in BANK_FX_VIEWS:
        assert has("SELECT", view) and not any(has(p, view) for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"))
    assert bank_fx_sequence_privileges(env) == {(s, "USAGE"): True for s in ("bank_fx_snapshots_id_seq", "bank_fx_rates_id_seq")} | \
        {(s, "SELECT"): True for s in ("bank_fx_snapshots_id_seq", "bank_fx_rates_id_seq")} | {(s, "UPDATE"): False for s in ("bank_fx_snapshots_id_seq", "bank_fx_rates_id_seq")}
    with env.admin.connect() as c:  # column-level grants stay absent too
        for table, column in (("bank_fx_snapshots", "source_title"), ("bank_fx_rates", "interbank_buy"), ("bank_fx_rates", "institution_name")):
            assert not one(c, "SELECT has_column_privilege(:r, :t, :col, 'UPDATE')", r=env.role, t=f"{env.schema}.{table}", col=column)


def test_bank_fx_objects_are_owned_by_the_admin_role_and_the_application_role_owns_nothing(env):
    with env.admin.connect() as c:
        owners = {r[0]: r[1] for r in c.execute(text(
            "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:s AND c.relname LIKE 'bank\\_fx\\_%'"), {"s": env.schema})}
        fns = {r[0]: r[1] for r in c.execute(text(
            "SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=:s AND p.proname LIKE 'bank\\_fx\\_%'"), {"s": env.schema})}
    assert set(BANK_FX + BANK_FX_VIEWS) <= set(owners) and fns
    assert set(owners.values()) == {env.admin_user} and set(fns.values()) == {env.admin_user}


def synthetic_bank_fx(vid=22, text_value="10.0001"):
    import bank_fx_builders as b
    from datetime import datetime, timezone
    from app.services.boz_bank_fx import parse_bank_fx_document
    banks = b.default_banks()
    banks[0][1] = text_value
    return parse_bank_fx_document(b.document(b.node(vid=vid, banks=banks)), retrieved_at=datetime(2030, 1, 3, tzinfo=timezone.utc))


def test_the_application_role_can_insert_and_read_a_complete_synthetic_snapshot_through_the_store(env):
    from app.services.bank_fx_store import save_bank_fx_snapshots
    assert save_bank_fx_snapshots(synthetic_bank_fx(), engine=env.app).inserted == 1
    assert save_bank_fx_snapshots(synthetic_bank_fx(), engine=env.app).already_present == 1  # the same-revision comparison reads as the app role
    assert save_bank_fx_snapshots(synthetic_bank_fx(vid=23), engine=env.app).inserted == 1  # a new revision: a second snapshot
    with env.app.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM bank_fx_snapshots") == 2 and one(c, "SELECT COUNT(*) FROM bank_fx_rates") == 18
        assert one(c, "SELECT COUNT(*) FROM bank_fx_canonical_snapshots") == 1


def test_the_application_role_cannot_modify_destroy_or_bypass_bank_fx_history(env):
    from app.services.bank_fx_store import save_bank_fx_snapshots
    save_bank_fx_snapshots(synthetic_bank_fx(), engine=env.app)
    def state():
        with env.admin.connect() as c:
            return [one(c, f"SELECT COUNT(*) FROM {t}") for t in BANK_FX]
    before = state()
    for table in BANK_FX:
        denied(env.app, f"TRUNCATE {table}")
        denied(env.app, f"TRUNCATE {table} CASCADE")
        denied(env.app, f"DROP TABLE {table}")
        denied(env.app, f"DROP TABLE {table} CASCADE")
        denied(env.app, f"ALTER TABLE {table} ADD COLUMN sneaky INTEGER")
        denied(env.app, f"ALTER TABLE {table} DISABLE TRIGGER ALL")
        denied(env.app, f"ALTER TABLE {table} OWNER TO {env.role}")
        denied(env.app, f"DELETE FROM {table}")
        with env.app.begin() as c:
            c.execute(text(f"GRANT ALL ON {table} TO {env.role}"))  # only a warning: nothing is granted without a grant option
    denied(env.app, "UPDATE bank_fx_snapshots SET source_title='x'")
    denied(env.app, "UPDATE bank_fx_rates SET interbank_buy_text='9.9'")
    denied(env.app, "DROP TRIGGER bank_fx_rates_immutable ON bank_fx_rates")
    denied(env.app, "DROP VIEW bank_fx_canonical_snapshots")
    denied(env.app, "DROP FUNCTION bank_fx_reject_change() CASCADE")
    denied(env.app, "CREATE OR REPLACE FUNCTION bank_fx_reject_change() RETURNS trigger AS $$ BEGIN RETURN NEW; END; $$ LANGUAGE plpgsql")
    denied(env.app, "SET session_replication_role = replica")  # would disable the immutability triggers if it were allowed
    with env.admin.connect() as c:
        for table in BANK_FX:
            for priv in ("TRUNCATE", "UPDATE", "DELETE", "REFERENCES", "TRIGGER"):
                assert not one(c, "SELECT has_table_privilege(:r, :t, :p)", r=env.role, t=f"{env.schema}.{table}", p=priv), (table, priv)
        assert one(c, "SELECT COUNT(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relname LIKE 'bank\\_fx\\_%' AND NOT t.tgisinternal AND t.tgenabled='O'") == 5
        assert one(c, "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='sneaky'") == 0
    assert state() == before


def test_rerunning_015_keeps_the_bank_fx_grants_unchanged_and_safe(env):
    before = (bank_fx_grants(env), bank_fx_sequence_privileges(env))
    assert before[0] == [("bank_fx_canonical_snapshots", "SELECT"), ("bank_fx_rates", "INSERT"), ("bank_fx_rates", "SELECT"),
                         ("bank_fx_snapshots", "INSERT"), ("bank_fx_snapshots", "SELECT")]
    with env.admin.begin() as c:
        c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
        c.execute(text((INIT / "015_application_role.sql").read_text()))
    assert (bank_fx_grants(env), bank_fx_sequence_privileges(env)) == before
    denied(env.app, "UPDATE bank_fx_rates SET interbank_buy_text='9.9'")  # a rerun never reopens the generic legacy UPDATE grant
    with env.admin.begin() as c:  # 015 also repairs an over-granted Bank FX table: grants are revoked, then re-established
        c.execute(text(f"GRANT UPDATE, DELETE, TRUNCATE ON bank_fx_rates TO {env.role}"))
        c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
        c.execute(text((INIT / "015_application_role.sql").read_text()))
    assert (bank_fx_grants(env), bank_fx_sequence_privileges(env)) == before


def test_017_alone_regrants_only_its_own_objects_and_leaves_the_rest_of_the_role_untouched(env):
    with env.admin.connect() as c:
        legacy_before = c.execute(text("SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee=:r AND table_schema=:s "
                                       "AND table_name NOT LIKE 'bank\\_fx\\_%' ORDER BY 1,2"), {"r": env.role, "s": env.schema}).all()
    with env.admin.begin() as c:
        c.execute(text(f"GRANT UPDATE, DELETE ON bank_fx_snapshots TO {env.role}"))  # drift on a Bank FX table only
        c.execute(text(f"SET LOCAL zm.app_role = '{env.role}'"))
        c.execute(text((INIT / "017_bank_fx.sql").read_text()))
    with env.admin.connect() as c:
        legacy_after = c.execute(text("SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee=:r AND table_schema=:s "
                                      "AND table_name NOT LIKE 'bank\\_fx\\_%' ORDER BY 1,2"), {"r": env.role, "s": env.schema}).all()
    assert legacy_after == legacy_before and ("bank_fx_snapshots", "UPDATE") not in bank_fx_grants(env)
