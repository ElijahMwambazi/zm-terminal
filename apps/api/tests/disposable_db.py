"""Fail-closed safety guard for PostgreSQL tests that create and drop objects.

The decision is made on the *parsed* URL (SQLAlchemy URL utilities), never on string suffixes, and runs before any engine is created
or any connection is opened. Policy:
  * driver: exactly `postgresql+psycopg` (psycopg 3, the only PostgreSQL driver in requirements.txt). Plain `postgresql` would resolve
    to psycopg2, which is not installed, and other dialects (pg8000, asyncpg, ...) interpret query parameters such as `database=`
    differently, so every other driver is refused rather than reasoned about;
  * database: the parsed database component must be non-empty, match the `_test` naming rule and not be the normal `zm_terminal`;
  * query: NO query parameters at all. Driver and libpq options (dbname, service, host, hostaddr, port, user, password, options, ...)
    can silently redirect the connection, so none is allowed; nothing the destructive tests need is expressed as a URL parameter.
"""

import re
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

PROTECTED_DATABASES = frozenset({"zm_terminal"})
DISPOSABLE_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*_test$")  # repository convention: disposable databases end in _test
SUPPORTED_DRIVERNAMES = frozenset({"postgresql+psycopg"})


class UnsafeDatabaseURL(ValueError):
    pass


def parse_disposable_database_url(raw: str) -> URL:
    """Return the parsed URL if and only if it names a disposable PostgreSQL test database; otherwise raise UnsafeDatabaseURL."""
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError, TypeError) as exc:
        raise UnsafeDatabaseURL("TEST_DATABASE_URL is not a parseable database URL") from exc
    if url.drivername not in SUPPORTED_DRIVERNAMES:
        raise UnsafeDatabaseURL(f"unsupported driver {url.drivername!r}; destructive tests accept only {sorted(SUPPORTED_DRIVERNAMES)}")
    name = url.database
    if not name:
        raise UnsafeDatabaseURL("TEST_DATABASE_URL names no database")
    if name in PROTECTED_DATABASES:
        raise UnsafeDatabaseURL(f"refusing to use the normal database {name!r}")
    if not DISPOSABLE_NAME.fullmatch(name):
        raise UnsafeDatabaseURL(f"database {name!r} is not a disposable test database (name must match {DISPOSABLE_NAME.pattern})")
    if url.query:
        raise UnsafeDatabaseURL(f"query parameters {sorted(url.query)} are not allowed: they can redirect the connection away from {name!r}")
    return url


def require_test_database_url() -> URL:
    """The guarded TEST_DATABASE_URL, or a skip when unset. Parsed and validated here, before any engine or connection exists."""
    import os

    import pytest

    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL not set")
    try:
        return parse_disposable_database_url(raw)
    except UnsafeDatabaseURL as exc:
        pytest.fail(str(exc), pytrace=False)


@contextmanager
def isolated_schema(url: URL, prefix: str = "t"):
    """A uniquely named schema in the disposable database. Everything a test creates (tables, views, trigger and helper functions,
    sequences, indexes) lives in it, and DROP SCHEMA ... CASCADE removes all of it, so repeated runs always start clean.
    Yields (schema, engine); the engine's connections have search_path set to the schema. `url` must already be guarded."""
    schema = f"{prefix}_{uuid4().hex[:12]}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield schema, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@contextmanager
def isolated_database(url: URL, prefix: str = "t"):
    """A uniquely named, empty database created from template0 on the guarded disposable server, dropped (WITH FORCE) afterwards.
    Migrations install into its `public` schema exactly as in production, so objects that are bound to `public` are tested as
    deployed, and dropping the database removes tables, views, trigger/helper functions, stored role settings and everything else.
    Yields (database_name, url_for_it, engine). The derived name is itself checked by the same fail-closed guard."""
    name = f"zm_{prefix}_{uuid4().hex[:12]}_test"
    derived = parse_disposable_database_url(url.set(database=name))
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    engine = create_engine(derived)
    try:
        yield name, derived, engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def reset_public(engine) -> None:
    """Return a disposable database's `public` schema to a pristine state: every table, view, sequence, trigger and helper function in
    it is removed, and the schema is recreated as PostgreSQL's template provides it (owned by pg_database_owner, USAGE for PUBLIC)."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("ALTER SCHEMA public OWNER TO pg_database_owner"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
