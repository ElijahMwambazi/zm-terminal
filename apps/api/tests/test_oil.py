import asyncio
import importlib
import io
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_oil
from app.main import app
from app.schemas.oil import OIL_STALE_AFTER_MONTHS, OilObservation, OilPoint
from app.services import oil_store, worldbank_oil
from app.services.provider_policy import PROVIDER_POLICIES, WORLD_BANK_OIL_POLICY
from app.services.worldbank_oil import WorldBankOilError, parse_workbook

router_module = importlib.import_module("app.api.router")
NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
TODAY = date(2026, 11, 14)
WORKBOOK_URL = "https://thedocs.worldbank.org/en/doc/abc123/related/CMO-Historical-Data-Monthly.xlsx"
PAGE_HTML = f'<html><a href="{WORKBOOK_URL}">Monthly prices</a><br>November 2026 (XLS)</html>'

# Contiguous months 2025M12..2026M10 as (key, average, Brent, Dubai, WTI, coal); unrelated columns carry junk that
# must never be read. WTI is "not published" (the workbook's "…") for the first month.
def make_rows(**overrides):
    prices = {"2025M12": (62.7, "…"), "2026M01": (66.8, 60.3), "2026M09": (116.8, 96.9), "2026M10": (125, 97.7)}
    prices.update(overrides)
    rows = []
    for key in ["2025M12"] + [f"2026M{m:02d}" for m in range(1, 11)]:
        brent, wti = prices.get(key, (70.0 + int(key[-2:]), 65.0 + int(key[-2:])))
        rows.append((key, "junk", brent, "junk", wti, "junk"))
    return rows


ROWS = make_rows()


def workbook(rows=ROWS, *, sheet="Monthly Prices", title="World Bank Commodity Price Data (The Pink Sheet)",
             subtitle="monthly prices in nominal US dollars, 1960 to present", updated="Updated on November 03, 2026",
             brent="Crude oil, Brent", wti="Crude oil, WTI", units=("($/bbl)", "($/bbl)"), extra_header=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append([title]); ws.append([subtitle]); ws.append(["(monthly series are available only in nominal US dollars)"])
    ws.append([updated])
    ws.append([None, "Crude oil, average", brent, "Crude oil, Dubai", wti, "Coal, Australian"] + (extra_header or []))
    ws.append([None, "($/bbl)", units[0], "($/bbl)", units[1], "($/mt)"])
    for row in rows:
        ws.append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def points(book=None):
    return {(p.benchmark, p.period_start.isoformat()): p for p in (book or parse_workbook(workbook())).observations}


# ---------------- workbook parsing ----------------
def test_parses_brent_and_wti_months_with_exact_decimals_and_month_periods():
    book = parse_workbook(workbook())
    got = points(book)
    assert str(got[("BRENT", "2026-09-01")].value) == "116.8" and str(got[("WTI", "2026-09-01")].value) == "96.9"
    assert str(got[("BRENT", "2026-10-01")].value) == "125"  # integer cell stays exact
    october = got[("BRENT", "2026-10-01")]
    assert (october.period_start, october.period_end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert (october.currency, october.unit, october.source, october.price_basis) == ("USD", "barrel", "world_bank", "monthly_average")
    assert (book.publication_date, book.first_month, book.last_month) == (date(2026, 11, 3), date(2025, 12, 1), date(2026, 10, 1))


def test_missing_marker_is_not_published_never_zero_and_months_are_not_filled():
    book = parse_workbook(workbook())
    assert ("WTI", "2025-12-01") not in points(book) and ("BRENT", "2025-12-01") in points(book)
    assert book.missing_cells == 1 and len(book.observations) == 21
    assert {p.period_start.month for p in book.observations if p.benchmark == "WTI"} == set(range(1, 11))


def test_only_brent_and_wti_are_read():
    book = parse_workbook(workbook())
    assert {p.benchmark for p in book.observations} == {"BRENT", "WTI"}  # junk in coal/average/Dubai columns ignored


def test_leap_february_and_december_period_ends():
    book = parse_workbook(workbook([("2024M01", 1, 80.0, 1, 75.0, "x"), ("2024M02", 1, 81.0, 1, 76.0, "x"),
                                    ("2024M03", 1, 82.0, 1, 77.0, "x")]))
    assert points(book)[("BRENT", "2024-02-01")].period_end == date(2024, 2, 29)
    book = parse_workbook(workbook([("2025M12", 1, 80.0, 1, 75.0, "x"), ("2026M01", 1, 81.0, 1, 76.0, "x")]))
    assert points(book)[("WTI", "2025-12-01")].period_end == date(2025, 12, 31)


def test_negative_value_is_not_rejected_by_the_adapter():
    book = parse_workbook(workbook([("2026M01", 1, 80.0, 1, -3.5, "x")]))
    assert str(points(book)[("WTI", "2026-01-01")].value) == "-3.5"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(value):
    """A real XLSX cannot hold these (openpyxl writes them as empty cells), so the guard is tested directly."""
    with pytest.raises(WorldBankOilError):
        worldbank_oil._normalize_cell(value)


def test_trailing_blank_rows_are_ignored():
    assert parse_workbook(workbook(ROWS + [(None,) * 6, (None,) * 6])).last_month == date(2026, 10, 1)


@pytest.mark.parametrize("kwargs", [
    {"sheet": "Prices"},                                           # unexpected sheet
    {"brent": "Crude oil, Brent (Europe)"},                        # renamed row/column label
    {"wti": "Crude oil, West Texas"},
    {"units": ("($/mt)", "($/bbl)")},                              # unexpected unit
    {"units": ("($/bbl)", "(cents/bbl)")},
    {"title": "World Bank Commodity Price Data"},                  # unexpected title
    {"subtitle": "annual prices"},
    {"updated": "Updated sometime"},                               # no usable publication date
    {"updated": "Updated on Smarch 03, 2026"},
    {"extra_header": ["Crude oil, Brent"]},                        # duplicate Brent label
    {"extra_header": ["Crude oil, WTI"]},                          # duplicate WTI label
])
def test_layout_changes_fail_closed(kwargs):
    with pytest.raises(WorldBankOilError):
        parse_workbook(workbook(**kwargs))


@pytest.mark.parametrize("rows", [
    [("2026M01", 1, 80.0, 1, 75.0, "x"), ("2026M03", 1, 81.0, 1, 76.0, "x")],     # gap
    [("2026M01", 1, 80.0, 1, 75.0, "x"), ("2026M01", 1, 81.0, 1, 76.0, "x")],     # duplicate month
    [("2026M02", 1, 80.0, 1, 75.0, "x"), ("2026M01", 1, 81.0, 1, 76.0, "x")],     # out of order
    [("2026-01", 1, 80.0, 1, 75.0, "x")],                                          # bad month key
    [("2026M13", 1, 80.0, 1, 75.0, "x")],
    [("Source: World Bank", 1, 80.0, 1, 75.0, "x")],                               # trailing text row
    [("2026M01", 1, "abc", 1, 75.0, "x")],                                         # junk cell
    [("2026M01", 1, 80.0, 1, "..", "x")],                                          # not the verified missing marker
    [("2026M01", 1, True, 1, 75.0, "x")],
    [("2026M01", 1, 80.123, 1, 75.0, "x")],                                        # hidden precision
    [("2026M01", 1, 80.0, 1, 1e12, "x")],                                          # absurd magnitude
    [],                                                                            # no data rows
])
def test_malformed_rows_fail_closed(rows):
    with pytest.raises(WorldBankOilError):
        parse_workbook(workbook(rows))


def test_corrupt_non_workbook_and_decompression_bomb_are_rejected(monkeypatch):
    for blob in (b"not a workbook", b"PK\x03\x04garbage", b""):
        with pytest.raises(WorldBankOilError):
            parse_workbook(blob)
    monkeypatch.setattr(worldbank_oil, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(WorldBankOilError):
        parse_workbook(workbook())


# ---------------- discovery and download (mocked HTTP; no network) ----------------
def run(coro):
    return asyncio.run(coro)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def serving(page=PAGE_HTML, book=None, seen=None):
    book = workbook() if book is None else book
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.host == "www.worldbank.org":
            return httpx.Response(200, text=page)
        if request.url.host == "thedocs.worldbank.org":
            return httpx.Response(200, content=book)
        return httpx.Response(404)
    return handler


def test_discovers_the_single_official_workbook_link():
    async def go():
        async with mock_client(serving()) as client:
            return await worldbank_oil.discover_workbook_url(client)
    assert run(go()) == WORKBOOK_URL


@pytest.mark.parametrize("page", [
    "<html>nothing here</html>",
    f'<a href="{WORKBOOK_URL}">a</a><a href="https://thedocs.worldbank.org/en/doc/zzz/related/CMO-Historical-Data-Monthly.xlsx">b</a>',
    '<a href="https://evil.example/en/doc/abc/related/CMO-Historical-Data-Monthly.xlsx">x</a>',      # unofficial host
    '<a href="http://thedocs.worldbank.org/en/doc/abc/related/CMO-Historical-Data-Monthly.xlsx">x</a>',  # not https
    '<a href="https://thedocs.worldbank.org/en/doc/abc/related/CMO-Historical-Data-Annual.xlsx">x</a>',  # wrong file
])
def test_discovery_rejects_missing_ambiguous_or_unofficial_links(page):
    async def go():
        async with mock_client(serving(page=page)) as client:
            return await worldbank_oil.discover_workbook_url(client)
    with pytest.raises(WorldBankOilError):
        run(go())


def test_same_link_repeated_is_not_ambiguous():
    page = f'<a href="{WORKBOOK_URL}">a</a><a href="{WORKBOOK_URL}">b</a>'
    async def go():
        async with mock_client(serving(page=page)) as client:
            return await worldbank_oil.discover_workbook_url(client)
    assert run(go()) == WORKBOOK_URL


def test_download_enforces_host_size_type_and_redirects(monkeypatch):
    async def fetch(url, handler):
        async with mock_client(handler) as client:
            return await worldbank_oil.download_workbook(client, url)
    assert run(fetch(WORKBOOK_URL, serving()))[:2] == b"PK"
    with pytest.raises(WorldBankOilError):
        run(fetch("https://evil.example/CMO-Historical-Data-Monthly.xlsx", serving()))
    with pytest.raises(WorldBankOilError):
        run(fetch(WORKBOOK_URL, lambda request: httpx.Response(200, content=b"<html>not xlsx</html>")))
    monkeypatch.setattr(worldbank_oil, "MAX_XLSX_BYTES", 100)
    with pytest.raises(WorldBankOilError):
        run(fetch(WORKBOOK_URL, serving()))
    monkeypatch.undo()
    def redirect(request):
        if request.url.host == "thedocs.worldbank.org":
            return httpx.Response(302, headers={"location": "https://evil.example/x.xlsx"})
        return httpx.Response(200, content=workbook())
    with pytest.raises(WorldBankOilError):
        run(fetch(WORKBOOK_URL, redirect))
    with pytest.raises(httpx.HTTPStatusError):
        run(fetch(WORKBOOK_URL, lambda request: httpx.Response(503)))


# ---------------- freshness (monthly publication cadence) ----------------
def test_staleness_is_months_behind_not_daily_market_status():
    assert OIL_STALE_AFTER_MONTHS == 2
    assert oil_store.months_behind(date(2026, 10, 1), TODAY) == 1
    assert not oil_store.is_stale(date(2026, 10, 1), today=TODAY)   # normal: previous month
    assert not oil_store.is_stale(date(2026, 9, 1), today=TODAY)    # one release late, still tolerated
    assert oil_store.is_stale(date(2026, 8, 1), today=TODAY)        # more than two months behind
    assert not oil_store.is_stale(date(2025, 12, 1), today=date(2026, 2, 28))
    assert oil_store.is_stale(date(2025, 10, 1), today=date(2026, 2, 28))  # year boundary


@pytest.mark.parametrize("range_,expected", [("1Y", date(2025, 11, 1)), ("3Y", date(2023, 11, 1)), ("5Y", date(2021, 11, 1)), ("ALL", None)])
def test_range_start_is_a_month_boundary(range_, expected):
    assert oil_store.range_start(range_, TODAY) == expected


# ---------------- API (offline) ----------------
client = TestClient(app)


def obs(benchmark="BRENT", month=date(2026, 10, 1), value="125.0", stale=False):
    last = {10: 31, 9: 30}[month.month]
    return OilObservation(
        benchmark=benchmark, period_start=month, period_end=month.replace(day=last), value=Decimal(value),
        name=f"{benchmark} crude oil", description="d", source_series="Crude oil, Brent", source_url=WORKBOOK_URL,
        publication_date=date(2026, 11, 3), retrieved_at=NOW, stale=stale)


def test_latest_returns_stored_months_with_period_and_provenance(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_latest_oil", lambda today: [obs("BRENT", value="125.0"), obs("WTI", value="-3.5")])
    body = client.get("/api/v1/oil/latest").json()
    assert body["missing_benchmarks"] == []
    brent, wti = body["observations"]
    assert (brent["period_start"], brent["period_end"], brent["value"]) == ("2026-10-01", "2026-10-31", "125.0")
    assert (brent["currency"], brent["unit"], brent["source"], brent["price_basis"]) == ("USD", "barrel", "world_bank", "monthly_average")
    assert (brent["publication_date"], brent["stale"], brent["stale_after_months"]) == ("2026-11-03", False, 2)
    assert brent["retrieved_at"].endswith("Z") and brent["source_url"] == WORKBOOK_URL and "observed_date" not in brent
    assert wti["value"] == "-3.5"


def test_empty_database_is_a_normal_empty_state(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_latest_oil", lambda today: [])
    assert client.get("/api/v1/oil/latest").json() == {"observations": [], "missing_benchmarks": ["BRENT", "WTI"]}
    assert client.get("/api/v1/oil/BRENT").status_code == 404
    monkeypatch.setattr(router_module, "fetch_oil_history", lambda b, r, today: [])
    assert client.get("/api/v1/oil/history").json() == {"benchmark": "BRENT", "range": "1Y", "count": 0, "observations": []}


def test_one_benchmark_missing_is_listed_not_fabricated(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_latest_oil", lambda today: [obs("BRENT")])
    body = client.get("/api/v1/oil/latest").json()
    assert body["missing_benchmarks"] == ["WTI"] and client.get("/api/v1/oil/WTI").status_code == 404
    assert client.get("/api/v1/oil/BRENT").json()["benchmark"] == "BRENT"


def test_history_is_stored_only_and_validates_inputs(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("oil API must never call the World Bank")
    monkeypatch.setattr(worldbank_oil, "download_workbook", no_network)
    monkeypatch.setattr(worldbank_oil, "discover_workbook_url", no_network)
    seen = {}
    def history(benchmark, range_, today):
        seen.update(benchmark=benchmark, range=range_)
        return [OilPoint(benchmark="WTI", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), value=Decimal("96.9"))]
    monkeypatch.setattr(router_module, "fetch_oil_history", history)
    body = client.get("/api/v1/oil/history?benchmark=WTI&range=3Y").json()
    assert seen == {"benchmark": "WTI", "range": "3Y"} and body["count"] == 1
    assert body["observations"][0]["period_start"] == "2026-09-01" and body["observations"][0]["value"] == "96.9"
    for bad in ("benchmark=GOLD", "range=1M", "range=2Y", "benchmark=brent"):
        assert client.get(f"/api/v1/oil/history?{bad}").status_code == 422
    assert client.get("/api/v1/oil/GOLD").status_code == 422 and client.get("/api/v1/oil/brent").status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/oil/latest", "/api/v1/oil/BRENT", "/api/v1/oil/history"])
def test_database_failure_is_a_safe_503(monkeypatch, path):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 125.0")
    monkeypatch.setattr(router_module, "fetch_latest_oil", boom)
    monkeypatch.setattr(router_module, "fetch_oil_history", boom)
    response = client.get(path)
    assert response.status_code == 503 and "SECRET" not in response.text and "125.0" not in response.text


def test_history_route_is_not_swallowed_by_the_benchmark_route(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_history", lambda b, r, today: [])
    monkeypatch.setattr(router_module, "fetch_latest_oil", lambda today: [])
    assert "observations" in client.get("/api/v1/oil/history").json()
    assert "missing_benchmarks" in client.get("/api/v1/oil/latest").json()


# ---------------- policy ----------------
def test_world_bank_policy_records_licence_provenance_and_stored_only_use():
    p = WORLD_BANK_OIL_POLICY
    assert p.dataset_id in PROVIDER_POLICIES and (p.provider_id, p.authentication) == ("world_bank", "none")
    assert (p.raw_response_storage, p.normalized_storage_allowed, p.attribution_required) == ("disabled", True, True)
    assert set(p.required_timestamps) == {"period_start", "period_end", "publication_date", "retrieved_at"}
    assert {"source_series", "source_url", "unit", "currency"} <= set(p.required_provenance)
    assert "CC BY 4.0" in p.request_guidance and "0038238" in p.request_guidance
    assert not [k for k in PROVIDER_POLICIES if "fred" in k]


# ---------------- import job ----------------
def saver(monkeypatch):
    calls = []
    def save(points, **kw):
        calls.append((points, kw))
        return (len(points), 0)
    monkeypatch.setattr(backfill_oil, "save_oil_observations", save)
    return calls


def import_(start, end=None, *, dry_run=False, book=None, seen=None, **kw):
    async def go():
        async with mock_client(serving(book=book, seen=seen)) as c:
            return await backfill_oil.run_import(start, end, dry_run=dry_run, client=c, now=NOW, **kw)
    return run(go())


def test_import_counts_and_single_atomic_batch_with_release_metadata(monkeypatch):
    calls = saver(monkeypatch)
    result = import_(date(2025, 12, 1), date(2026, 10, 1))
    assert result == {"months_requested": 11, "selected": 21, "not_published": 1, "inserted": 21, "already_present": 0}
    assert len(calls) == 1
    points, kw = calls[0]
    assert kw["publication_date"] == date(2026, 11, 3) and kw["source_url"] == WORKBOOK_URL and kw["retrieved_at"] == NOW
    assert all(date(2025, 12, 1) <= p.period_start <= date(2026, 10, 1) for p in points)


def test_import_defaults_to_the_newest_workbook_month_and_dry_run_writes_nothing(monkeypatch):
    calls = saver(monkeypatch)
    result = import_(date(2026, 9, 1), dry_run=True)
    assert result["selected"] == 4 and result["inserted"] == 0 and calls == []


def test_import_makes_exactly_two_requests_and_rejects_out_of_range_months(monkeypatch):
    saver(monkeypatch)
    seen = []
    import_(date(2026, 9, 1), seen=seen)
    assert seen == ["https://www.worldbank.org/en/research/commodity-markets", WORKBOOK_URL]
    for start, end in [(date(2026, 9, 1), date(2026, 12, 1)), (date(1900, 1, 1), None), (date(2026, 10, 1), date(2026, 9, 1))]:
        with pytest.raises(backfill_oil.BackfillError):
            import_(start, end)


def test_import_propagates_layout_failures_without_writing(monkeypatch):
    calls = saver(monkeypatch)
    with pytest.raises(WorldBankOilError):
        import_(date(2026, 9, 1), book=workbook(brent="Crude oil, Brent (Europe)"))
    assert calls == []


def test_cli_prints_counts_only_and_hides_failures(monkeypatch, capsys):
    async def ok(*a, **k):
        return {"months_requested": 2, "selected": 4, "not_published": 0, "inserted": 0, "already_present": 0}
    monkeypatch.setattr(backfill_oil, "run_import", ok)
    assert backfill_oil.main(["--from", "2026-09", "--to", "2026-10", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run months_requested=2 selected=4 not_published=0 inserted=0 already_present=0"
    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 125.0")
    monkeypatch.setattr(backfill_oil, "run_import", boom)
    assert backfill_oil.main(["--from", "2026-09"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err and "125.0" not in err
    async def conflict(*a, **k):
        raise oil_store.OilConflictError(2)
    monkeypatch.setattr(backfill_oil, "run_import", conflict)
    assert backfill_oil.main(["--from", "2026-09"]) == 1 and "2 stored" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        backfill_oil.main(["--from", "2026-9"])      # monthly inputs must be YYYY-MM
    with pytest.raises(SystemExit):
        backfill_oil.main(["--from", "2026-09-01"])  # not daily dates


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
        c.execute(text("DROP TABLE IF EXISTS global_oil_prices"))
        c.execute(text((Path(__file__).parents[3] / "db/init/009_global_oil_prices.sql").read_text()))
    yield eng
    eng.dispose()


def pt(month, value, benchmark="BRENT"):
    year, mon = month
    first = date(year, mon, 1)
    last = date(year + (mon == 12), mon % 12 + 1, 1).toordinal() - 1
    return OilPoint(benchmark=benchmark, period_start=first, period_end=date.fromordinal(last), value=Decimal(value))


def save(points, engine, pub=date(2026, 11, 3)):
    return oil_store.save_oil_observations(points, publication_date=pub, source_url=WORKBOOK_URL, retrieved_at=NOW, engine=engine)


def test_pg_insert_is_idempotent_numeric_equal_and_retains_provenance(engine):
    points = [pt((2026, 9), "116.8"), pt((2026, 10), "125"), pt((2026, 10), "97.7", "WTI")]
    assert save(points, engine) == (3, 0)
    assert save(points, engine) == (0, 3)
    assert save([pt((2026, 10), "125.00")], engine) == (0, 1)  # numerically equal
    with engine.connect() as c:
        rows = c.execute(text("SELECT * FROM global_oil_prices ORDER BY benchmark, period_start")).mappings().all()
    assert len(rows) == 3 and not set(rows[0]) & {"payload", "raw", "raw_payload", "workbook"}
    first = rows[0]
    assert (first["provider"], first["currency"], first["unit"], str(first["value"])) == ("world_bank", "USD", "barrel", "116.8")
    assert (first["period_start"], first["period_end"], first["publication_date"]) == (date(2026, 9, 1), date(2026, 9, 30), date(2026, 11, 3))
    assert first["source_series"] == "Crude oil, Brent" and first["source_url"] == WORKBOOK_URL and first["retrieved_at"] == NOW
    assert rows[2]["source_series"] == "Crude oil, WTI"


def test_pg_conflict_rolls_back_the_whole_batch_and_never_overwrites(engine):
    save([pt((2026, 9), "116.8")], engine)
    with pytest.raises(oil_store.OilConflictError) as exc:
        save([pt((2026, 10), "125"), pt((2026, 9), "99.9")], engine, pub=date(2026, 12, 3))
    assert exc.value.count == 1 and "99.9" not in str(exc.value)
    with engine.connect() as c:
        rows = c.execute(text("SELECT period_start, value, publication_date FROM global_oil_prices")).all()
    assert [(r[0], str(r[1]), r[2]) for r in rows] == [(date(2026, 9, 1), "116.8", date(2026, 11, 3))]  # new month rolled back, old kept


def test_pg_conflicts_are_per_benchmark_identity_so_benchmarks_do_not_collide(engine):
    save([pt((2026, 10), "125"), pt((2026, 10), "97.7", "WTI")], engine)
    assert save([pt((2026, 10), "125"), pt((2026, 10), "97.7", "WTI")], engine) == (0, 2)


def test_pg_negative_values_store_safely(engine):
    save([pt((2026, 4), "-3.5", "WTI")], engine)
    assert [str(p.value) for p in oil_store.fetch_oil_history("WTI", "ALL", today=TODAY, engine=engine)] == ["-3.5"]


def test_pg_history_orders_filters_ranges_and_never_fills(engine):
    months = [(2020, 3), (2021, 11), (2023, 10), (2024, 6), (2025, 11), (2025, 12), (2026, 10)]  # gaps stay gaps
    save([pt(m, "60") for m in reversed(months)], engine)
    save([pt((2026, 10), "1", "WTI")], engine)
    got = lambda r: [(p.period_start.year, p.period_start.month) for p in oil_store.fetch_oil_history("BRENT", r, today=TODAY, engine=engine)]
    assert got("ALL") == months  # ascending regardless of insert order
    assert got("1Y") == months[4:]      # from 2025-11-01 inclusive
    assert got("3Y") == months[3:]      # from 2023-11-01: 2023-10 excluded
    assert got("5Y") == months[1:]      # from 2021-11-01 inclusive: 2020-03 excluded
    assert all(p.benchmark == "BRENT" for p in oil_store.fetch_oil_history("BRENT", "ALL", today=TODAY, engine=engine))
    assert oil_store.fetch_oil_history("WTI", "1Y", today=date(2030, 1, 1), engine=engine) == []  # empty is normal


def test_pg_latest_is_newest_month_per_benchmark_with_stale_flag(engine):
    save([pt((2026, 9), "116.8"), pt((2026, 10), "125"), pt((2026, 5), "90", "WTI")], engine)
    latest = {o.benchmark: o for o in oil_store.fetch_latest_oil(today=TODAY, engine=engine)}
    assert latest["BRENT"].period_start == date(2026, 10, 1) and str(latest["BRENT"].value) == "125" and latest["BRENT"].stale is False
    assert latest["WTI"].period_start == date(2026, 5, 1) and latest["WTI"].stale is True
    assert latest["BRENT"].name == "Brent crude oil" and latest["BRENT"].publication_date == date(2026, 11, 3)
    assert [o.benchmark for o in oil_store.fetch_latest_oil(today=TODAY, engine=engine)] == ["BRENT", "WTI"]


def test_pg_schema_rejects_non_calendar_months_and_unknown_values(engine):
    insert = ("INSERT INTO global_oil_prices (benchmark, provider, period_start, period_end, value, currency, unit, "
              "publication_date, source_series, source_url, retrieved_at) VALUES "
              "(:b, :p, :s, :e, 1, :c, :u, '2026-11-03', 'x', 'u', now())")
    ok = dict(b="BRENT", p="world_bank", s="2026-10-01", e="2026-10-31", c="USD", u="barrel")
    for patch in ({"e": "2026-10-30"}, {"s": "2026-10-02", "e": "2026-11-01"}, {"e": "2026-11-30"},
                  {"b": "GOLD"}, {"p": "fred"}, {"c": "ZMW"}, {"u": "litre"}):
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(text(insert), {**ok, **patch})
    with engine.begin() as c:
        c.execute(text(insert), ok)  # the valid row is accepted


def test_pg_import_end_to_end_is_idempotent_and_conflict_aware(engine):
    first = import_(date(2025, 12, 1), date(2026, 10, 1), engine=engine)
    assert (first["inserted"], first["already_present"]) == (21, 0)
    again = import_(date(2025, 12, 1), date(2026, 10, 1), engine=engine)
    assert (again["inserted"], again["already_present"]) == (0, 21)
    revised = workbook(make_rows(**{"2026M09": (117.0, 96.9)}))  # Brent September revised upstream
    with pytest.raises(oil_store.OilConflictError):
        import_(date(2025, 12, 1), date(2026, 10, 1), book=revised, engine=engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT value FROM global_oil_prices WHERE benchmark='BRENT' AND period_start='2026-09-01'")).scalar() == Decimal("116.8")
