import importlib

import pytest


@pytest.fixture(autouse=True)
def no_database_by_default(monkeypatch):
    """Offline tests never touch PostgreSQL unless they opt in explicitly."""
    router = importlib.import_module("app.api.router")
    monkeypatch.setattr(router, "save_fx_quote", lambda quote: True)
    monkeypatch.setattr(router, "save_tbill_auctions", lambda rows: (0, 0))
    monkeypatch.setattr(router, "save_bond_auctions", lambda rows: (0, 0))


@pytest.fixture(scope="session")
def disposable_database():
    """One empty database created for the whole session on the guarded TEST_DATABASE_URL server (skips when unset) and dropped at the
    end. Tests that install migrations reset its `public` schema first (disposable_db.reset_public), so every test starts clean."""
    from disposable_db import isolated_database, require_test_database_url

    with isolated_database(require_test_database_url(), "shared") as (_name, url, engine):
        yield url, engine
