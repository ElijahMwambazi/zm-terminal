import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.boz import parse_boz_quote
from app.services import boz


router_module = importlib.import_module("app.api.router")


def test_route_through_real_service_with_mock_transport(monkeypatch):
    # An old observation is intentional: the entire route must return stale data.
    row = {
        "buying": "19.1000", "mid_rate": "19.1251", "selling": "19.1500",
        "time": '<time datetime="2000-01-01T07:30:51Z">ignored</time>',
    }
    async def fetch():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
        async with httpx.AsyncClient(transport=transport) as client:
            return await boz.get_boz_fx_quote(client=client)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    with TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW")
    assert response.status_code == 200
    assert response.json()["mid"] == "19.1251"
    assert response.json()["observed_at"] == "2000-01-01T07:30:51Z"
    assert response.json()["stale"] is True


def test_fx_endpoint_contract(monkeypatch):
    payload = json.loads((Path(__file__).parent / "fixtures/boz_usd_rates.json").read_text())
    quote = parse_boz_quote(payload, retrieved_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
    async def fetch():
        return quote
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    with TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW")
    assert response.status_code == 200
    assert response.json() == quote.model_dump(mode="json")
    assert set(response.json()) == {
        "symbol", "base_currency", "quote_currency", "buy", "mid", "sell",
        "observed_at", "retrieved_at", "source", "source_url", "stale",
    }
    assert response.json()["mid"] == "19.8987"
    assert response.json()["stale"] is True


@pytest.mark.parametrize("error,status,detail", [
    (ValueError("<html>private payload</html>"), 502, "Bank of Zambia data unavailable or invalid"),
    (httpx.ConnectError("internal host details"), 502, "Bank of Zambia data unavailable or invalid"),
    (httpx.ReadTimeout("internal timeout details"), 504, "Bank of Zambia request timed out"),
])
def test_safe_upstream_errors(monkeypatch, error, status, detail):
    async def fetch():
        raise error
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    with TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW")
    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_existing_routes_remain_independent_of_boz(monkeypatch):
    async def unexpected_boz_call():
        raise AssertionError("Existing endpoints must not call BoZ")
    async def ready():
        return True
    async def coverage():
        return {"title": "OpenBB API", "route_count": 1, "sample_routes": ["/test"]}
    monkeypatch.setattr(router_module, "get_boz_fx_quote", unexpected_boz_call)
    monkeypatch.setattr(router_module, "database_is_ready", lambda: False)
    monkeypatch.setattr(router_module, "openbb_is_ready", ready)
    monkeypatch.setattr(router_module, "get_openbb_coverage", coverage)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/api/v1/system").json() == {"api": True, "database": False, "openbb": True}
        instruments = client.get("/api/v1/instruments")
        assert instruments.status_code == 503
        assert instruments.json() == {"detail": "Database is not ready"}
        assert client.get("/api/v1/markets/overview").json() == {
            "status": "scaffold", "message": "Market data adapters are not connected yet.",
            "symbols": ["USDZMW", "LASI", "CECZ", "ZNCO", "BTCUSD", "BTCZMW", "COPPER"],
        }
        assert client.get("/api/v1/openbb/coverage").json() == {
            "title": "OpenBB API", "route_count": 1, "sample_routes": ["/test"],
        }
        assert client.get("/api/v1/fx/EURZMW").status_code == 404
