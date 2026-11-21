"""The disposable-database guard: pure URL parsing, no database and no engine are ever created here."""

import pytest

import disposable_db
from disposable_db import UnsafeDatabaseURL, parse_disposable_database_url

BASE = "postgresql+psycopg://u:placeholder@localhost:5432/"


@pytest.mark.parametrize("url,name", [
    (BASE + "zm_energy_test", "zm_energy_test"),
    ("postgresql+psycopg://zm_terminal:zm_terminal_dev@localhost:5432/zm_terminal_test", "zm_terminal_test"),  # the CI database
    ("postgresql+psycopg://u:p%40ss%2Fword@db.example:5433/other_scratch_test", "other_scratch_test"),  # escaped password characters
])
def test_accepts_supported_driver_test_databases_without_query(url, name):
    assert parse_disposable_database_url(url).database == name


@pytest.mark.parametrize("url", [
    BASE + "zm_terminal",
    BASE + "zm_terminal?application_name=energy_test",  # query-parameter bypass of the old suffix check
    BASE + "zm_terminal?application_name=zm_energy_test",
    BASE + "zm_terminal?options=-csearch_path%3Denergy_test",
    BASE + "zm_terminal?options=-c%20application_name%3Denergy_test%20_test",
    BASE + "zm_terminal?x=/zm_energy_test",  # a slash inside the query must not be mistaken for a path
    BASE + "?dbname=zm_energy_test",  # no database component; a query value must not stand in for it
    BASE,
    BASE.rstrip("/"),
    BASE + "ZM_ENERGY_TEST",  # uppercase is not the convention
    BASE + "_test",
    BASE + "zm_energy_test_old",
    BASE + "test",
])
def test_rejects_non_disposable_or_ambiguous_database_names(url):
    with pytest.raises(UnsafeDatabaseURL):
        parse_disposable_database_url(url)


@pytest.mark.parametrize("query", [
    "dbname=zm_terminal", "database=zm_terminal", "service=prod", "servicefile=/etc/pg_service.conf",
    "application_name=energy_test", "options=-csearch_path%3Dx", "host=other.example", "hostaddr=10.0.0.1", "port=5433",
    "user=zm_terminal", "password=other", "sslmode=disable", "target_session_attrs=any",
])
def test_every_query_parameter_is_rejected_even_on_a_disposable_database(query):
    with pytest.raises(UnsafeDatabaseURL, match="query parameters"):
        parse_disposable_database_url(BASE + "zm_energy_test?" + query)


@pytest.mark.parametrize("url", [
    "postgresql+pg8000://u:placeholder@localhost/zm_energy_test?database=zm_terminal",  # pg8000 would connect to zm_terminal
    "postgresql+pg8000://u:placeholder@localhost/zm_energy_test",
    "postgresql+asyncpg://u:placeholder@localhost/zm_energy_test",
    "postgresql+psycopg2://u:placeholder@localhost/zm_energy_test",
    "postgresql+psycopg_async://u:placeholder@localhost/zm_energy_test",
    "postgresql+unknowndriver://u:placeholder@localhost/zm_energy_test",
    "postgresql://u:placeholder@localhost/zm_energy_test",  # resolves to psycopg2, which this repository does not install
    "postgres://u:placeholder@localhost/zm_energy_test",
    "sqlite:///zm_energy_test", "mysql://u@h/zm_energy_test",
])
def test_rejects_unsupported_drivers_and_dialects(url):
    with pytest.raises(UnsafeDatabaseURL, match="unsupported driver|not a parseable"):
        parse_disposable_database_url(url)


@pytest.mark.parametrize("url", ["", "not a url", "postgresql+psycopg://[::1/zm_energy_test", "postgresql+psycopg://:@:/"])
def test_rejects_malformed_urls(url):
    with pytest.raises(UnsafeDatabaseURL):
        parse_disposable_database_url(url)


def test_decision_follows_the_parsed_database_not_the_string():
    url = BASE + "zm_terminal?application_name=energy_test"
    assert url.endswith("_test")  # the old raw-suffix check would have accepted this
    with pytest.raises(UnsafeDatabaseURL, match="normal database"):
        parse_disposable_database_url(url)


@pytest.mark.parametrize("url", [
    "postgresql+pg8000://u:placeholder@localhost/zm_energy_test?database=zm_terminal",
    BASE + "zm_terminal?application_name=energy_test",
    BASE + "zm_energy_test?dbname=zm_terminal",
])
def test_rejected_urls_fail_before_any_engine_or_connection(monkeypatch, url):
    def forbidden(*a, **k):
        raise AssertionError("an engine was created before the guard decided")
    monkeypatch.setattr(disposable_db, "create_engine", forbidden)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    with pytest.raises(pytest.fail.Exception):
        disposable_db.require_test_database_url()


def test_postgres_test_files_use_the_shared_guard_not_a_raw_suffix_check():
    """Static regression: no PostgreSQL test file may decide database safety from the raw URL string (rsplit / .endswith("_test")) or read TEST_DATABASE_URL itself."""
    import re
    from pathlib import Path

    legacy = re.compile(r"""rsplit\(\s*["']/["']\s*,\s*1\s*\)\s*\[\s*-1\s*\]|\.endswith\(\s*["']_test["']\s*\)|os\.(?:environ\.get|getenv|environ\[)\s*\(?\s*["']TEST_DATABASE_URL["']""")
    tests = Path(__file__).parent
    offenders = [
        f"{path.relative_to(tests)}:{number}"
        for path in sorted(tests.rglob("*.py"))
        if path.name not in {"disposable_db.py", Path(__file__).name}
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if legacy.search(line.split("#", 1)[0])
    ]
    assert not offenders, f"use disposable_db.require_test_database_url() instead: {offenders}"
