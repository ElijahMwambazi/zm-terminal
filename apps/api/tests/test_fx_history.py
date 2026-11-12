import importlib
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.fx import FxHistoryPoint, FxQuote
from app.services import fx_store
from app.services.provider_policy import BOZ_USD_POLICY

router_module = importlib.import_module("app.api.router")
NOW = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)


def quote(observed=NOW - timedelta(hours=1), mid="19.8987"):
    return FxQuote(buy="19.8736", mid=mid, sell="19.9236", observed_at=observed,
                   retrieved_at=NOW, stale=False)


def point(hours_ago, mid="19.9000"):
    return FxHistoryPoint(observed_at=NOW - timedelta(hours=hours_ago), buy="19.8", mid=mid, sell="20.0")


# ---- policy ----
def test_boz_policy_values():
    p = BOZ_USD_POLICY
    assert (p.provider_id, p.authentication, p.personal_data_expected) == ("boz", "none", False)
    assert p.raw_response_storage == "disabled" and p.normalized_storage_allowed
    assert p.attribution_required and p.redistribution == "review_required"
    assert p.commercial_use == "review_required"
    assert set(p.required_provenance) == {"source", "source_url"}
    assert set(p.required_timestamps) == {"observed_at", "retrieved_at"}


def test_providers_endpoint():
    with TestClient(app) as client:
        body = client.get("/api/v1/providers").json()
    assert [(p["provider_id"], p["dataset_id"]) for p in body] == [
        ("boz", "boz_zmw_usd_daily_exchange_rates"), ("boz", "boz_treasury_bill_auction_results"),
        ("boz", "boz_government_bond_auction_results")]


# ---- range handling ----
@pytest.mark.parametrize("range_,days", [("1D", 1), ("1W", 7), ("1M", 30), ("3M", 90), ("1Y", 365)])
def test_range_start(range_, days):
    assert fx_store.range_start(range_, NOW) == NOW - timedelta(days=days)


def test_range_all_has_no_lower_bound():
    assert fx_store.range_start("ALL", NOW) is None


@pytest.mark.parametrize("bad", ["2D", "1d", "", "MAX", "1M;DROP"])
def test_bad_range_rejected(bad):
    with TestClient(app) as client:
        assert client.get("/api/v1/fx/USDZMW/history", params={"range": bad}).status_code == 422


# ---- history route ----
def test_history_empty(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_fx_history", lambda r, now: [])
    with TestClient(app) as client:
        body = client.get("/api/v1/fx/USDZMW/history?range=1W").json()
    assert body == {"symbol": "USDZMW", "range": "1W", "count": 0, "observations": []}


def test_history_default_range_shape_and_decimal_strings(monkeypatch):
    seen = {}
    def fetch(r, now):
        seen["range"] = r
        return [point(3, "19.8000"), point(1, "19.9100")]
    monkeypatch.setattr(router_module, "fetch_fx_history", fetch)
    with TestClient(app) as client:
        body = client.get("/api/v1/fx/USDZMW/history").json()
    assert seen["range"] == "1M" and body["range"] == "1M" and body["count"] == 2
    first = body["observations"][0]
    assert set(first) == {"observed_at", "buy", "mid", "sell", "source"}
    assert first["mid"] == "19.8000" and isinstance(first["buy"], str)  # trailing zeros kept
    assert first["observed_at"].endswith("Z") and first["source"] == "boz"


def test_history_storage_failure_is_safe_503(monkeypatch):
    def boom(r, now):
        raise RuntimeError("postgresql://user:secret@host/db")
    monkeypatch.setattr(router_module, "fetch_fx_history", boom)
    with TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW/history")
    assert response.status_code == 503
    assert response.json() == {"detail": "Stored history unavailable"}


# ---- persistence hook in the quote route ----
def test_quote_is_persisted(monkeypatch):
    saved = []
    async def fetch():
        return quote()
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    monkeypatch.setattr(router_module, "save_fx_quote", lambda q: saved.append(q) or True)
    with TestClient(app) as client:
        assert client.get("/api/v1/fx/USDZMW").status_code == 200
    assert saved == [quote()]


def test_persistence_failure_still_returns_quote_and_logs_no_secrets(monkeypatch, caplog):
    async def fetch():
        return quote()
    def boom(q):
        raise RuntimeError("postgresql://user:secret@host/db")
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    monkeypatch.setattr(router_module, "save_fx_quote", boom)
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW")
    assert response.status_code == 200 and response.json()["mid"] == "19.8987"
    assert "RuntimeError" in caplog.text and "secret" not in caplog.text


def test_upstream_failure_does_not_persist(monkeypatch):
    saved = []
    async def fetch():
        raise ValueError("bad")
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    monkeypatch.setattr(router_module, "save_fx_quote", lambda q: saved.append(q))
    with TestClient(app) as client:
        assert client.get("/api/v1/fx/USDZMW").status_code == 502
    assert saved == []


# ---- real PostgreSQL (opt-in: TEST_DATABASE_URL must point at a disposable DB) ----
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    schema = open(os.path.join(os.path.dirname(__file__), "../../../db/init/001_schema.sql")).read()
    extra = open(os.path.join(os.path.dirname(__file__), "../../../db/init/002_fx_rates_provenance.sql")).read()
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_rates"))
        c.execute(text(schema))
        c.execute(text(extra))
        c.execute(text("DELETE FROM fx_rates"))
    yield eng
    eng.dispose()


def test_pg_idempotent_exact_decimals_no_raw_payload(engine):
    q = quote(mid="19.8900")
    assert fx_store.save_fx_quote(q, engine=engine) is True
    assert fx_store.save_fx_quote(q, engine=engine) is False
    with engine.connect() as c:
        rows = c.execute(text("SELECT * FROM fx_rates")).mappings().all()
        cols = set(rows[0])
    assert len(rows) == 1
    assert rows[0]["mid"] == Decimal("19.8900") and str(rows[0]["mid"]) == "19.8900"
    assert rows[0]["source"] == "boz" and rows[0]["retrieved_at"] == NOW
    assert rows[0]["source_url"] == q.source_url
    assert not cols & {"payload", "raw", "raw_payload", "response"}


def test_pg_history_order_filter_and_duplicates(engine):
    for hours_ago in (50, 2, 30 * 24, 400 * 24):  # inserted out of order
        fx_store.save_fx_quote(quote(observed=NOW - timedelta(hours=hours_ago)), engine=engine)
    fx_store.save_fx_quote(quote(observed=NOW - timedelta(hours=2)), engine=engine)  # duplicate
    def hist(r):
        return fx_store.fetch_fx_history(r, now=NOW, engine=engine)
    assert [p.observed_at for p in hist("ALL")] == sorted(p.observed_at for p in hist("ALL"))
    assert len(hist("ALL")) == 4
    assert len(hist("1D")) == 1
    assert len(hist("1W")) == 2
    assert len(hist("1M")) == 3  # exactly 30 days old is inclusive
    assert len(hist("1Y")) == 3
    assert isinstance(hist("1D")[0].mid, Decimal)


def test_pg_empty_history(engine):
    assert fx_store.fetch_fx_history("ALL", now=NOW, engine=engine) == []


# ---- conflict-aware live save ----
def test_conflict_logs_safe_warning_and_still_returns_live_quote(monkeypatch, caplog):
    async def fetch():
        return quote(mid="19.8987")
    def conflict(q):
        raise fx_store.FxConflictError("1 stored observation(s) conflict with BoZ values, e.g. 2026-10-09T14:00:00+00:00")
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    monkeypatch.setattr(router_module, "save_fx_quote", conflict)
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.get("/api/v1/fx/USDZMW")
    assert response.status_code == 200 and response.json()["mid"] == "19.8987"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
        "USDZMW persistence conflict; live quote returned unsaved"]
    assert "19.8987" not in caplog.text and "2026-10-09" not in caplog.text


def test_identical_save_logs_nothing(monkeypatch, caplog):
    async def fetch():
        return quote()
    monkeypatch.setattr(router_module, "get_boz_fx_quote", fetch)
    monkeypatch.setattr(router_module, "save_fx_quote", lambda q: False)  # already present
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert client.get("/api/v1/fx/USDZMW").status_code == 200
    assert caplog.records == []


def test_pg_live_save_conflict_never_overwrites(engine):
    original = quote(mid="19.8987")
    assert fx_store.save_fx_quote(original, engine=engine) is True
    changed = FxQuote(buy="19.8736", mid="20.0000", sell="19.9236", observed_at=original.observed_at,
                      retrieved_at=NOW + timedelta(minutes=5), stale=False)
    with pytest.raises(fx_store.FxConflictError):
        fx_store.save_fx_quote(changed, engine=engine)
    with engine.connect() as c:
        row = c.execute(text("SELECT mid, retrieved_at FROM fx_rates")).one()
    assert row.mid == Decimal("19.8987") and row.retrieved_at == NOW


def test_pg_live_save_equivalent_scale_is_already_present(engine):
    assert fx_store.save_fx_quote(FxQuote(buy="19.8", mid="19.9", sell="20.0", observed_at=NOW - timedelta(hours=1),
                                          retrieved_at=NOW, stale=False), engine=engine) is True
    same = FxQuote(buy="19.8000", mid="19.9000", sell="20.0000", observed_at=NOW - timedelta(hours=1),
                   retrieved_at=NOW + timedelta(minutes=1), stale=False)
    assert fx_store.save_fx_quote(same, engine=engine) is False


def test_pg_live_and_backfill_share_conflict_rules(engine):
    q = quote(mid="19.8987")
    fx_store.save_fx_quote(q, engine=engine)
    with pytest.raises(fx_store.FxConflictError):
        fx_store.save_fx_observations({q.observed_at: (q.buy, Decimal("1"), q.sell)}, retrieved_at=NOW,
                                      source="boz", source_url=q.source_url, engine=engine)
