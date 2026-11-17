"""Migration 015: the API's application role versus the admin/owner role. PostgreSQL tests in a disposable database; SYNTHETIC data.

Everything runs in a throwaway schema (001-013 + 014 + 015) with a throwaway, randomly named application role created by the test
and dropped afterwards (SET zm.app_role), so neither the normal database nor the real `zm_terminal_app` role is ever touched.
The application engine logs in as that role over a real connection; a superuser `SET ROLE` would not prove the same thing.
"""

import secrets
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from disposable_db import isolated_schema, require_test_database_url
from test_energy_vintage_schema import (NOW, SERIES, add_import, add_obs, add_total, candidate, link_candidate, one, publish, sha,
                                        station, supersede_import)
from app.schemas.fx import FxQuote
from app.services import fx_store

INIT = Path(__file__).parents[3] / "db/init"
ENERGY = ("energy_generation_group_totals", "energy_generation_observations", "energy_generation_imports",
          "energy_publication_candidates", "energy_publications", "energy_source_stations")
ENERGY_VIEWS = ("energy_publication_current_imports", "energy_generation_canonical")
INSUFFICIENT_PRIVILEGE = "42501"


class Env:
    def __init__(self, schema, admin, app, role, admin_user):
        self.schema, self.admin, self.app, self.role, self.admin_user = schema, admin, app, role, admin_user


@pytest.fixture
def env():
    url = require_test_database_url()
    role = "zm_t_app_" + secrets.token_hex(4)
    password = secrets.token_hex(16)
    with isolated_schema(url, "role_t") as (schema, admin):
        with admin.begin() as c:
            for migration in sorted(INIT.glob("0[01][0-9]_*.sql")):
                if migration.name.startswith("015"):
                    c.execute(text(f"SET LOCAL zm.app_role = '{role}'"))
                c.execute(text(migration.read_text()))
            c.execute(text(f"ALTER ROLE {role} PASSWORD '{password}'"))
            admin_user = one(c, "SELECT current_user")
        app = create_engine(url.set(username=role, password=password), connect_args={"options": f"-csearch_path={schema}"})
        try:
            yield Env(schema, admin, app, role, admin_user)
        finally:
            app.dispose()
            with admin.begin() as c:  # schema is dropped by isolated_schema; the cluster-wide role is ours alone to remove
                c.execute(text(f"DROP SCHEMA \"{schema}\" CASCADE"))
                c.execute(text(f"DROP OWNED BY {role}"))
                c.execute(text(f"DROP ROLE {role}"))
                c.execute(text(f'CREATE SCHEMA "{schema}"'))  # recreated empty so isolated_schema's own DROP succeeds


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
def test_application_role_can_run_the_full_importer_lifecycle(env):
    app = env.app
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
    with pytest.raises(DBAPIError, match="owns objects"):
        with env.admin.begin() as c:
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
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename NOT LIKE 'energy\\_%'"))]
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
