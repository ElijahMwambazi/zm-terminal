"""The disposable-database guard: pure URL parsing, no database and no engine are ever created here."""

import pytest

import disposable_db
from disposable_db import UnsafeDatabaseURL, parse_disposable_database_url

BASE = "postgresql+psycopg://zm_terminal:zm_terminal_dev@localhost:5432/"


@pytest.mark.parametrize("url,name", [
    (BASE + "zm_energy_test", "zm_energy_test"),
    ("postgresql://u:p@db.example:5433/zm_terminal_test", "zm_terminal_test"),  # CI database
    ("postgresql+psycopg2://u@localhost/other_scratch_test", "other_scratch_test"),
    (BASE + "zm_energy_test?application_name=zm_terminal", "zm_energy_test"),  # harmless query parameters stay harmless
])
def test_accepts_disposable_test_databases(url, name):
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
    BASE + "zm_energy_test?dbname=zm_terminal",  # libpq parameter that would redirect the connection
    BASE + "zm_energy_test?service=prod",
    BASE + "ZM_ENERGY_TEST",  # uppercase is not the convention
    BASE + "_test",
    BASE + "zm_energy_test_old",
    BASE + "test",
])
def test_rejects_non_disposable_or_ambiguous_targets(url):
    with pytest.raises(UnsafeDatabaseURL):
        parse_disposable_database_url(url)


@pytest.mark.parametrize("url", [
    "", "not a url", "postgresql://[::1/zm_energy_test", "sqlite:///zm_energy_test", "mysql://u@h/zm_energy_test",
    "postgresql+psycopg://:@:/",
])
def test_rejects_malformed_or_unsupported_urls(url):
    with pytest.raises(UnsafeDatabaseURL):
        parse_disposable_database_url(url)


def test_decision_follows_the_parsed_database_not_the_string(monkeypatch):
    # a URL whose raw text ends in "_test" but whose parsed database is the normal one is refused
    url = BASE + "zm_terminal?application_name=energy_test"
    assert url.endswith("_test") and not url.rsplit("/", 1)[-1].startswith("zm_energy")
    with pytest.raises(UnsafeDatabaseURL, match="normal database"):
        parse_disposable_database_url(url)


def test_the_guard_fails_before_any_engine_or_connection(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("an engine was created before the guard decided")
    monkeypatch.setattr(disposable_db, "create_engine", forbidden)
    monkeypatch.setenv("TEST_DATABASE_URL", BASE + "zm_terminal?application_name=energy_test")
    with pytest.raises(pytest.fail.Exception, match="normal database"):
        disposable_db.require_test_database_url()
