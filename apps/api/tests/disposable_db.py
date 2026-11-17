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
