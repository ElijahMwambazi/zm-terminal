import asyncio
import importlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_oil
from app.main import app
from app.schemas.oil import OIL_SERIES, OilPoint
from app.services import oil_store, openbb_oil
from app.services.provider_policy import FRED_OIL_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
FIXTURE = (Path(__file__).parent / "fixtures" / "openbb_fred_brent.json").read_text()
TODAY = date(2026, 11, 14)
NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def payload(rows, benchmark="BRENT") -> str:
    series = OIL_SERIES[benchmark].series_id
    return json.dumps({"results": [{"symbol": series, "unit": "Dollars per Barrel", **r} for r in rows]})


def pt(day, value, benchmark="BRENT") -> OilPoint:
    return OilPoint(benchmark=benchmark, observed_date=day, value=Decimal(value))


# ---------------- parsing ----------------
def test_parses_fixture_preserving_decimals_and_leaving_weekend_gap():
    points = openbb_oil.parse_oil_payload(FIXTURE, "BRENT", today=TODAY)
    assert [(p.observed_date.isoformat(), str(p.value)) for p in points] == [
        ("2026-10-29", "82.35"), ("2026-10-30", "80.0"), ("2026-11-02", "79.1")]
    assert all((p.currency, p.unit, p.source, p.benchmark) == ("USD", "barrel", "fred", "BRENT") for p in points)
    assert len(points) == 3  # Sat/Sun 10-31/11-01 are absent, never filled


def test_integer_and_negative_wti_values_are_valid():
    text_ = payload([{"date": "2020-04-20", "price": -36.98}, {"date": "2020-04-21", "price": 10}], "WTI")
    points = openbb_oil.parse_oil_payload(text_, "WTI", today=TODAY)
    assert [str(p.value) for p in points] == ["-36.98", "10"]


def test_results_are_sorted_ascending():
    text_ = payload([{"date": "2026-11-02", "price": 2.5}, {"date": "2026-10-30", "price": 3.5}])
    assert [p.observed_date.isoformat() for p in openbb_oil.parse_oil_payload(text_, "BRENT", today=TODAY)] == ["2026-10-30", "2026-11-02"]


def test_empty_results_are_valid_and_mean_no_data():
    assert openbb_oil.parse_oil_payload('{"results": []}', "BRENT", today=TODAY) == []


@pytest.mark.parametrize("bad", [
    "not json", "[]", '{"results": null}', '{"detail": "x"}', '{"results": [1]}',
    payload([{"date": "2026-11-02", "price": 1.0, "symbol": "DCOILWTICO"}]),          # wrong series
    payload([{"date": "2026-11-02", "price": 1.0, "unit": "Dollars per Gallon"}]),     # wrong unit
    payload([{"date": "2026-11-02", "price": "79.1"}]),                                 # string price
    payload([{"date": "2026-11-02", "price": None}]),
    payload([{"date": "2026-11-02", "price": True}]),
    payload([{"date": "2026-11-02"}]),
    payload([{"date": "11/02/2026", "price": 79.1}]),
    payload([{"date": None, "price": 79.1}]),
    payload([{"date": "2026-11-15", "price": 79.1}]),                                   # future
    payload([{"date": "2026-11-02", "price": 79.1}, {"date": "2026-11-02", "price": 79.2}]),  # duplicate date
    '{"results": [{"date": "2026-11-02", "symbol": "DCOILBRENTEU", "unit": "Dollars per Barrel", "price": NaN}]}',
])
def test_malformed_payloads_are_rejected(bad):
    with pytest.raises(ValueError):
        openbb_oil.parse_oil_payload(bad, "BRENT", today=TODAY)


def test_fetch_requests_the_validated_openbb_route_and_series():
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, **dict(request.url.params))
        return httpx.Response(200, text=FIXTURE)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await openbb_oil.fetch_oil_series("BRENT", date(2026, 10, 1), date(2026, 11, 14), client=client)
    points = asyncio.run(go())
    assert seen == {"path": "/api/v1/fred/commodity/price/spot", "provider": "fred", "commodity": "brent",
                    "start_date": "2026-10-01", "end_date": "2026-11-14"}
    assert len(points) == 3


def test_series_identifiers_match_validated_openbb_fred_map():
    assert (OIL_SERIES["BRENT"].series_id, OIL_SERIES["WTI"].series_id) == ("DCOILBRENTEU", "DCOILWTICO")
    assert (OIL_SERIES["BRENT"].openbb_commodity, OIL_SERIES["WTI"].openbb_commodity) == ("brent", "wti")


def test_staleness_heuristic():
    assert not openbb_oil.is_stale(date(2026, 11, 4), today=TODAY)
    assert openbb_oil.is_stale(date(2026, 11, 3), today=TODAY)


# ---------------- API ----------------
client = TestClient(app)


def fake_fetch(by_benchmark):
    async def fake(benchmark, start, end, *, client=None):
        result = by_benchmark[benchmark]
        if isinstance(result, Exception):
            raise result
        return result
    return fake


def test_latest_returns_newest_per_benchmark_with_provenance(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({
        "BRENT": [pt(date(2026, 11, 12), "80.10"), pt(date(2026, 11, 13), "81.20")],
        "WTI": [pt(date(2026, 11, 13), "-3.50", "WTI")]}))
    body = client.get("/api/v1/oil/latest").json()
    assert body["missing_benchmarks"] == []
    brent, wti = body["observations"]
    assert (brent["benchmark"], brent["observed_date"], brent["value"]) == ("BRENT", "2026-11-13", "81.20")
    assert (brent["currency"], brent["unit"], brent["source"], brent["series_id"]) == ("USD", "barrel", "fred", "DCOILBRENTEU")
    assert brent["source_url"].endswith("/DCOILBRENTEU") and brent["retrieved_at"].endswith("Z") and isinstance(brent["stale"], bool)
    assert wti["value"] == "-3.50"


def test_latest_lists_a_benchmark_without_recent_data_as_missing(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"BRENT": [pt(date(2026, 11, 13), "81.20")], "WTI": []}))
    body = client.get("/api/v1/oil/latest").json()
    assert body["missing_benchmarks"] == ["WTI"] and len(body["observations"]) == 1


def test_stale_flag_when_upstream_lags(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"BRENT": [pt(date(2020, 1, 2), "60.00")], "WTI": []}))
    assert client.get("/api/v1/oil/latest").json()["observations"][0]["stale"] is True


def test_single_benchmark_route_and_404_when_nothing_recent(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"WTI": [pt(date(2026, 11, 13), "75.00", "WTI")]}))
    assert client.get("/api/v1/oil/WTI").json()["value"] == "75.00"
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"WTI": []}))
    assert client.get("/api/v1/oil/WTI").status_code == 404


@pytest.mark.parametrize("error,status", [
    (httpx.ReadTimeout("slow"), 504),
    (httpx.ConnectError("down"), 502),
    (httpx.HTTPStatusError("400", request=httpx.Request("GET", "http://x"), response=httpx.Response(400)), 502),
    (ValueError("Oil row has an unexpected unit"), 502),
])
def test_upstream_failures_map_to_safe_statuses(monkeypatch, error, status):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"BRENT": error, "WTI": []}))
    response = client.get("/api/v1/oil/latest")
    assert response.status_code == status
    assert "unexpected unit" not in response.text and "http://x" not in response.text


def test_persistence_failure_still_returns_live_data(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_series", fake_fetch({"BRENT": [pt(date(2026, 11, 13), "81.20")], "WTI": []}))
    def boom(points, **kw):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "save_oil_observations", boom)
    response = client.get("/api/v1/oil/latest")
    assert response.status_code == 200 and "SECRET" not in response.text
    def conflict(points, **kw):
        raise oil_store.OilConflictError(1)
    monkeypatch.setattr(router_module, "save_oil_observations", conflict)
    assert client.get("/api/v1/oil/BRENT").status_code == 200


def test_history_reads_storage_only_and_validates_params(monkeypatch):
    async def must_not_call(*a, **k):
        raise AssertionError("history must not call OpenBB")
    monkeypatch.setattr(router_module, "fetch_oil_series", must_not_call)
    seen = {}
    def fake_history(benchmark, range_, *, today):
        seen.update(benchmark=benchmark, range=range_)
        return [pt(date(2026, 11, 2), "79.1"), pt(date(2026, 11, 3), "78.9")]
    monkeypatch.setattr(router_module, "fetch_oil_history", fake_history)
    body = client.get("/api/v1/oil/history?benchmark=BRENT&range=3M").json()
    assert seen == {"benchmark": "BRENT", "range": "3M"} and body["count"] == 2
    assert [o["observed_date"] for o in body["observations"]] == ["2026-11-02", "2026-11-03"]
    assert client.get("/api/v1/oil/history").json()["range"] == "1Y"
    assert client.get("/api/v1/oil/history?benchmark=GOLD").status_code == 422
    assert client.get("/api/v1/oil/history?range=2Y").status_code == 422
    assert client.get("/api/v1/oil/GOLD").status_code == 422
    assert client.get("/api/v1/oil/brent").status_code == 422


def test_history_database_failure_is_safe_503(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_oil_history", boom)
    response = client.get("/api/v1/oil/history")
    assert response.status_code == 503 and "SECRET" not in response.text


# ---------------- policy ----------------
def test_fred_policy_records_provenance_and_open_review():
    p = FRED_OIL_POLICY
    assert p.dataset_id in PROVIDER_POLICIES and p.provider_id == "fred"
    assert (p.authentication, p.raw_response_storage, p.attribution_required) == ("api_key", "disabled", True)
    assert (p.redistribution, p.commercial_use) == ("review_required", "review_required")
    assert set(p.required_timestamps) == {"observed_date", "retrieved_at"} and "series_id" in p.required_provenance


# ---------------- backfill CLI ----------------
def test_backfill_validates_range_and_dry_run_skips_database(monkeypatch):
    calls = []
    async def fake(benchmark, start, end, *, client=None):
        calls.append(benchmark)
        return [pt(date(2026, 11, 2), "79.1", benchmark)]
    monkeypatch.setattr(backfill_oil, "fetch_oil_series", fake)
    def no_db(*a, **k):
        raise AssertionError("dry run must not write")
    monkeypatch.setattr(backfill_oil, "save_oil_observations", no_db)
    result = asyncio.run(backfill_oil.run_backfill(date(2026, 11, 1), date(2026, 11, 10), dry_run=True, now=NOW))
    assert result == {"fetched": 2, "inserted": 0, "already_present": 0} and calls == ["BRENT", "WTI"]
    for start, end in [(date(2026, 11, 10), date(2026, 11, 1)), (date(2026, 11, 1), date(2026, 11, 20)), (date(2000, 1, 1), date(2026, 11, 1))]:
        with pytest.raises(backfill_oil.BackfillError):
            asyncio.run(backfill_oil.run_backfill(start, end, now=NOW))


def test_backfill_cli_hides_failures_and_reports_counts(monkeypatch, capsys):
    async def ok(*a, **k):
        return {"fetched": 3, "inserted": 0, "already_present": 0}
    monkeypatch.setattr(backfill_oil, "run_backfill", ok)
    assert backfill_oil.main(["--from", "2026-11-01", "--to", "2026-11-10", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run fetched=3 inserted=0 already_present=0"
    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(backfill_oil, "run_backfill", boom)
    assert backfill_oil.main(["--from", "2026-11-01"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err
    async def conflict(*a, **k):
        raise oil_store.OilConflictError(2)
    monkeypatch.setattr(backfill_oil, "run_backfill", conflict)
    assert backfill_oil.main(["--from", "2026-11-01"]) == 1 and "2 stored" in capsys.readouterr().err


# ---------------- real PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS global_oil_benchmarks"))
        c.execute(text((Path(__file__).parents[3] / "db/init/009_global_oil_benchmarks.sql").read_text()))
    yield eng
    eng.dispose()


def test_pg_insert_is_idempotent_and_numeric_equal(engine):
    points = openbb_oil.parse_oil_payload(FIXTURE, "BRENT", today=TODAY)
    assert oil_store.save_oil_observations(points, retrieved_at=NOW, engine=engine) == (3, 0)
    assert oil_store.save_oil_observations(points, retrieved_at=NOW, engine=engine) == (0, 3)
    same_numeric = [pt(date(2026, 10, 30), "80.000")]  # 80.0 stored; numerically equal
    assert oil_store.save_oil_observations(same_numeric, retrieved_at=NOW, engine=engine) == (0, 1)
    with engine.connect() as c:
        rows = c.execute(text("SELECT * FROM global_oil_benchmarks ORDER BY observed_date")).mappings().all()
    assert len(rows) == 3 and rows[0]["source"] == "fred" and rows[0]["series_id"] == "DCOILBRENTEU"
    assert (rows[0]["currency"], rows[0]["unit"], str(rows[0]["value"])) == ("USD", "barrel", "82.35")
    assert rows[0]["retrieved_at"] == NOW and not set(rows[0]) & {"payload", "raw", "raw_payload"}


def test_pg_conflict_rolls_back_whole_batch_and_never_overwrites(engine):
    oil_store.save_oil_observations([pt(date(2026, 11, 2), "79.1")], retrieved_at=NOW, engine=engine)
    batch = [pt(date(2026, 11, 3), "70.0"), pt(date(2026, 11, 2), "99.9")]
    with pytest.raises(oil_store.OilConflictError) as exc:
        oil_store.save_oil_observations(batch, retrieved_at=NOW, engine=engine)
    assert exc.value.count == 1 and "99.9" not in str(exc.value)
    with engine.connect() as c:
        rows = c.execute(text("SELECT observed_date, value FROM global_oil_benchmarks")).all()
    assert [(r[0], str(r[1])) for r in rows] == [(date(2026, 11, 2), "79.1")]  # new row rolled back, old value kept


def test_pg_benchmarks_are_separate_and_negative_values_store(engine):
    oil_store.save_oil_observations([pt(date(2020, 4, 20), "-36.98", "WTI"), pt(date(2020, 4, 20), "26.60", "BRENT")], retrieved_at=NOW, engine=engine)
    wti = oil_store.fetch_oil_history("WTI", "ALL", today=TODAY, engine=engine)
    brent = oil_store.fetch_oil_history("BRENT", "ALL", today=TODAY, engine=engine)
    assert [str(p.value) for p in wti] == ["-36.98"] and [str(p.value) for p in brent] == ["26.60"]


def test_pg_history_orders_filters_ranges_and_never_fills(engine):
    days = [date(2021, 1, 4), date(2026, 7, 1), date(2026, 9, 1), date(2026, 10, 30), date(2026, 11, 2)]  # weekend/holiday gaps stay gaps
    oil_store.save_oil_observations([pt(d, "60") for d in reversed(days)], retrieved_at=NOW, engine=engine)
    oil_store.save_oil_observations([pt(date(2026, 11, 2), "1", "WTI")], retrieved_at=NOW, engine=engine)
    got = lambda r: [p.observed_date for p in oil_store.fetch_oil_history("BRENT", r, today=TODAY, engine=engine)]
    assert got("ALL") == days  # ascending regardless of insert order
    assert got("1M") == [date(2026, 10, 30), date(2026, 11, 2)]
    assert got("3M") == days[2:]
    assert got("1Y") == days[1:]
    assert got("5Y") == days[1:]
