import importlib

import pytest


@pytest.fixture(autouse=True)
def no_database_by_default(monkeypatch):
    """Offline tests never touch PostgreSQL unless they opt in explicitly."""
    router = importlib.import_module("app.api.router")
    monkeypatch.setattr(router, "save_fx_quote", lambda quote: True)
    monkeypatch.setattr(router, "save_tbill_auctions", lambda rows: (0, 0))
    monkeypatch.setattr(router, "save_bond_auctions", lambda rows: (0, 0))
