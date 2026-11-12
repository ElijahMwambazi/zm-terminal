import asyncio
import importlib
import io
import os
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_fuel
from app.main import app
from app.schemas.fuel import FuelHistoryPoint, FuelPrice
from app.services import erb_fuel, fuel_store
from app.services.erb_fuel import FuelArchiveError, normalize_price, parse_workbook
from app.services.provider_policy import ERB_FUEL_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
RETAIL_HEAD = ["Year", "Month", "Petrol (K/L)", "Diesel (K/L)", "Kerosene (K/L)", "Jet A-1 (K/L)", "Low Sulphur Gas oil (K/L)"]
WHOLE_HEAD = ["Year", "Month", "Petrol (K/L)", "Diesel (K/L)", "Kerosene (K/L)", "Jet A-1 (K/L)",
              "HFO (Kg/L)", "LPG (Kg/L)", "Bitumen (Kg/L)", "Low Sulphur Gas oil (K/L)", None, None]


def workbook(retail=(), wholesale=(), *, retail_name="Retail Prices ", wholesale_name="Wholesale Prices",
             retail_head=None, wholesale_head=None, fmt="0.00") -> bytes:
    """Build an offline test workbook. Cells are (value) or (value, number_format)."""
    wb = openpyxl.Workbook()
    wh = wb.active
    wh.title = wholesale_name
    wh.append(wholesale_head or WHOLE_HEAD)
    for row in wholesale:
        wh.append(list(row))
    rt = wb.create_sheet(retail_name)
    rt.append(retail_head or RETAIL_HEAD)
    rt.append([None] * 7)  # the real sheet has a blank second header row
    for row in retail:
        rt.append(list(row))
    for ws in (wh, rt):
        for r in ws.iter_rows(min_row=2):
            for c in r[2:]:
                if isinstance(c.value, (int, float)):
                    c.number_format = fmt
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def parse(**kw):
    return parse_workbook(workbook(**kw))


def by(rows, kind, product):
    return {r.effective_date: r for r in rows if r.price_type == kind and r.product == product}


RETAIL = [
    (2026, "September", 25.29, 26.86, 27.02, 28.71, 26.86),
    (2026, "October", 31.46, 33.27, 29.76, 31.85, 33.27),
    (2012, "January", 8.16, 7.57, 5.15, "-", "-"),
]


# ---------------- workbook parser ----------------
def test_prices_products_dates_and_types():
    rows, stats = parse(retail=RETAIL, wholesale=[(2026, "October", 23.13, 26.51, 26.29, 25.9, None, None, None, 26.51)])
    oct_ = date(2026, 10, 1)
    assert by(rows, "retail", "PETROL")[oct_].price_zmw == Decimal("31.46")
    assert by(rows, "retail", "DIESEL")[oct_].unit == "litre" and by(rows, "retail", "KEROSENE")[oct_].price_zmw == Decimal("29.76")
    assert by(rows, "retail", "JET_A1")[oct_].price_zmw == Decimal("31.85") and by(rows, "retail", "LSGO")[oct_].price_zmw == Decimal("33.27")
    assert by(rows, "wholesale", "PETROL")[oct_].price_zmw == Decimal("23.13") and by(rows, "wholesale", "LSGO")[oct_].price_type == "wholesale"
    assert set(by(rows, "retail", "PETROL")) == {date(2012, 1, 1), date(2026, 9, 1), oct_}
    assert stats.records_examined == 4 and stats.valid_rows == len(rows)


def test_binary_float_noise_is_normalized_to_displayed_two_decimals():
    rows, _ = parse(retail=[(2026, "March", 33.059999999999995, 21.06000000000001, 1, 2, 3)])
    got = by(rows, "retail", "PETROL")[date(2026, 3, 1)].price_zmw
    assert got == Decimal("33.06") and str(got) == "33.06" and str(by(rows, "retail", "DIESEL")[date(2026, 3, 1)].price_zmw) == "21.06"
    assert str(by(rows, "retail", "KEROSENE")[date(2026, 3, 1)].price_zmw) == "1.00"


def test_dash_blank_and_missing_are_not_published_never_zero():
    rows, stats = parse(retail=[(2012, "January", 8.16, 7.57, 5.15, "-", None)])
    assert date(2012, 1, 1) not in by(rows, "retail", "JET_A1") and date(2012, 1, 1) not in by(rows, "retail", "LSGO")
    assert stats.missing_cells == 2 and not any(r.price_zmw == 0 for r in rows)


def test_repeated_unchanged_months_are_kept_as_published():
    rows, _ = parse(retail=[(2026, "August", 25.29, 26.86, 27.02, 28.71, 26.86), (2026, "September", 25.29, 26.86, 27.02, 28.71, 26.86)])
    assert len(by(rows, "retail", "PETROL")) == 2  # the source explicitly contains both months


def test_identical_duplicates_collapse_and_conflicting_duplicates_are_excluded():
    rows, stats = parse(retail=[
        (2022, "January", 30.0, 20.0, 10.0, 5.0, 4.0), (2022, "January", 30.0, 21.5, 10.0, 5.0, 4.0)])
    jan = date(2022, 1, 1)
    assert by(rows, "retail", "PETROL")[jan].price_zmw == Decimal("30.00")  # identical: one row
    assert jan not in by(rows, "retail", "DIESEL")  # mid-month change, no day: ambiguous, never resolved by row order
    assert stats.identical_duplicate_cells == 4 and stats.ambiguous_duplicate_cells == 1


def test_hidden_precision_in_two_decimal_cell_is_rejected_not_rounded_and_general_is_displayed():
    rows, stats = parse(wholesale=[(2022, "January", 20.00448, 21.0, 18.5, 23.7, None, None, None, 21.0)])
    assert date(2022, 1, 1) not in by(rows, "wholesale", "PETROL") and stats.hidden_precision_cells == 1
    wb = openpyxl.load_workbook(io.BytesIO(workbook(wholesale=[(2022, "January", 20.00448, 21.4, 18.5, 23.7, None, None, None, 21.4)])))
    ws = wb["Wholesale Prices"]
    for c in ws[2][2:6]:
        c.number_format = "General"
    out = io.BytesIO(); wb.save(out)
    rows, stats = parse_workbook(out.getvalue())
    assert by(rows, "wholesale", "PETROL")[date(2022, 1, 1)].price_zmw == Decimal("20.00448") and stats.hidden_precision_cells == 0


@pytest.mark.parametrize("value,fmt,expected", [
    (33.059999999999995, "0.00", "33.06"), (1234.5, "#,##0.00", "1234.50"), (28, "General", "28.00"),
    (20.977439999999998, "General", "20.97744"), ("-", "0.00", None), (None, "General", None), ("  ", "0.00", None),
])
def test_normalize_price(value, fmt, expected):
    got = normalize_price(value, fmt)
    assert (None if got is None else str(got)) == expected


@pytest.mark.parametrize("value,fmt", [
    (-1.5, "0.00"), (0, "0.00"), (float("nan"), "0.00"), (float("inf"), "General"), ("abc", "0.00"), (True, "0.00"),
    (1.5, "0.000"), (1.5, "mmm-yy"),
])
def test_unusable_values_and_formats_fail(value, fmt):
    with pytest.raises(FuelArchiveError):
        normalize_price(value, fmt)


@pytest.mark.parametrize("kw", [
    dict(retail_name="Retail Prices 2"), dict(wholesale_name="Wholesale"),
    dict(retail_head=["Year", "Month", "Petrol", "Diesel (K/L)", "Kerosene (K/L)", "Jet A-1 (K/L)", "Low Sulphur Gas oil (K/L)"]),
    dict(wholesale_head=WHOLE_HEAD[:9] + ["LSGO (K/L)", None, None]),
    dict(retail_head=RETAIL_HEAD + ["Surprise"]),
])
def test_unexpected_schema_fails(kw):
    with pytest.raises(FuelArchiveError):
        parse(retail=RETAIL, **kw)


@pytest.mark.parametrize("row", [
    (2026, "Octob", 1.0, 1.0, 1.0, 1.0, 1.0), ("2026", "October", 1.0, 1.0, 1.0, 1.0, 1.0), (1999, "October", 1.0, 1.0, 1.0, 1.0, 1.0),
    (None, None, 1.0, 1.0, 1.0, 1.0, 1.0),
])
def test_bad_rows_fail_loudly(row):
    with pytest.raises(FuelArchiveError):
        parse(retail=[row])


@pytest.mark.parametrize("bad", [-3.0, 0, "n/a"])  # inf/nan cannot be stored in an XLSX; normalize_price covers them
def test_single_unusable_value_is_a_counted_rejection_not_a_price(bad):
    rows, stats = parse(retail=[(2026, "October", bad, 1.0, 1.0, 1.0, 1.0)])
    assert date(2026, 10, 1) not in by(rows, "retail", "PETROL") and stats.invalid_value_cells == 1
    assert stats.ambiguous_duplicate_cells == 0 and len(by(rows, "retail", "DIESEL")) == 1


def test_corrupt_workbook_fails_without_leaking():
    with pytest.raises(FuelArchiveError) as exc:
        parse_workbook(b"not an xlsx")
    assert "not an xlsx" not in str(exc.value)


# ---------------- retrieval ----------------
def zip_of(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def erb_transport(zip_bytes, listing=None):
    url = "https://www.erb.org.zm/wp-content/uploads/files/PUMP-AND-WHOLESALE-PRICES-2012-2027.zip"
    def handler(request):
        if "wp-json" in str(request.url):
            return httpx.Response(200, json=listing if listing is not None else [{"date": "2026-10-08T16:57:47", "source_url": url, "mime_type": "application/zip"}])
        assert str(request.url) == url
        return httpx.Response(200, content=zip_bytes)
    return httpx.MockTransport(handler), url


def fetch(zip_bytes, listing=None):
    transport, url = erb_transport(zip_bytes, listing)
    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            return await erb_fuel.fetch_archive(c)
    return asyncio.run(run()), url


def test_fetch_archive_discovers_url_and_extracts_workbook():
    xlsx = workbook(retail=RETAIL)
    (data, found), url = fetch(zip_of({"PUMP AND WHOLESALE PRICES.xlsx": xlsx}))
    assert found == url and data == xlsx


@pytest.mark.parametrize("zip_bytes,listing", [
    (zip_of({"a.xlsx": b"x", "b.xlsx": b"y"}), None),
    (zip_of({"notes.txt": b"x"}), None),
    (b"garbage", None),
    (zip_of({"a.xlsx": b"x"}), []),
    (zip_of({"a.xlsx": b"x"}), [{"date": "2026-01-01", "source_url": "https://evil.example/PUMP-AND-WHOLESALE-PRICES.zip"}]),
    (zip_of({"a.xlsx": b"x"}), [{"date": "2026-01-01", "source_url": "https://www.erb.org.zm/other.zip"}]),
])
def test_fetch_archive_rejects_unexpected_sources(zip_bytes, listing):
    with pytest.raises(FuelArchiveError):
        fetch(zip_bytes, listing)


# ---------------- job (offline, no database) ----------------
@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("database touched")
    monkeypatch.setattr(fuel_store, "get_engine", boom)


def run_job(start, end, xlsx, **kw):
    transport, _ = erb_transport(zip_of({"x.xlsx": xlsx}))
    async def go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await backfill_fuel.run_backfill(start, end, client=c, now=NOW, **kw)
    return asyncio.run(go())


def test_job_bounds_dry_run_counts_only():
    xlsx = workbook(retail=RETAIL)
    r = run_job(date(2026, 9, 1), date(2026, 10, 31), xlsx, dry_run=True)
    assert r["valid_rows"] == 13 and r["in_range"] == 10 and r["missing_cells"] == 2 and r["inserted"] == 0
    assert run_job(date(2026, 10, 1), date(2026, 10, 31), xlsx, dry_run=True)["in_range"] == 5
    assert run_job(date(2026, 1, 1), date(2026, 1, 31), xlsx, dry_run=True)["in_range"] == 0


def test_job_rejects_future_and_inverted_ranges_without_fetching():
    def handler(request):
        raise AssertionError("fetched")
    async def go(a, b):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await backfill_fuel.run_backfill(a, b, client=c, now=NOW, dry_run=True)
    with pytest.raises(ValueError, match="future"):
        asyncio.run(go(date(2026, 1, 1), date(2027, 1, 1)))
    with pytest.raises(ValueError, match="after"):
        asyncio.run(go(date(2026, 2, 1), date(2026, 1, 1)))


def test_cli_prints_counts_only_and_hides_failures(monkeypatch, capsys):
    async def fake(start, end, dry_run=False, **kw):
        return {"valid_rows": 3, "inserted": 0}
    monkeypatch.setattr(backfill_fuel, "run_backfill", fake)
    assert backfill_fuel.main(["--from", "2026-01-01", "--to", "2026-02-01", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run valid_rows=3 inserted=0"
    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db price 31.46")
    monkeypatch.setattr(backfill_fuel, "run_backfill", boom)
    assert backfill_fuel.main(["--from", "2026-01-01"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err and "31.46" not in err
    async def conflict(*a, **k):
        raise fuel_store.FuelConflictError(3)
    monkeypatch.setattr(backfill_fuel, "run_backfill", conflict)
    assert backfill_fuel.main(["--from", "2026-01-01"]) == 1 and "conflicts=3" in capsys.readouterr().err


# ---------------- policy ----------------
def test_erb_policy_is_a_separate_provider():
    p = ERB_FUEL_POLICY
    assert (p.provider_id, p.dataset_id) == ("erb", "erb_zambia_fuel_prices") and p.name == "Energy Regulation Board Zambia"
    assert (p.authentication, p.personal_data_expected, p.raw_response_storage) == ("none", False, "disabled")
    assert p.normalized_storage_allowed and p.attribution_required
    assert p.redistribution == p.commercial_use == "review_required"
    assert set(p.required_timestamps) == {"effective_date", "retrieved_at"} and p.dataset_id in PROVIDER_POLICIES


# ---------------- API (offline) ----------------
def price(product, day, value, **kw):
    return FuelPrice(product=product, effective_date=day, price_zmw=value, source_url="https://www.erb.org.zm/x.zip", retrieved_at=NOW, **kw)


def test_latest_product_history_validation_and_errors(monkeypatch):
    c = TestClient(app)
    monkeypatch.setattr(router_module, "fetch_latest_fuel", lambda: [price("PETROL", date(2026, 10, 1), "31.46"), price("LSGO", date(2026, 10, 1), "33.2")])
    body = c.get("/api/v1/fuel/latest").json()
    assert [p["product"] for p in body["prices"]] == ["PETROL", "LSGO"] and body["missing_products"] == ["DIESEL", "KEROSENE", "JET_A1"]
    first = body["prices"][0]
    assert first["price_zmw"] == "31.46" and body["prices"][1]["price_zmw"] == "33.2"  # exact decimal strings
    assert (first["currency"], first["unit"], first["price_type"], first["source"]) == ("ZMW", "litre", "retail", "erb")
    assert first["effective_date"] == "2026-10-01" and first["retrieved_at"].endswith("Z") and first["source_url"]
    assert c.get("/api/v1/fuel/PETROL").json()["price_zmw"] == "31.46"
    assert c.get("/api/v1/fuel/DIESEL").status_code == 404
    for bad in ("petrol", "JET", "COAL"):
        assert c.get(f"/api/v1/fuel/{bad}").status_code == 422
    seen = {}
    def hist(product, kind, rng, today):
        seen.update(product=product, kind=kind, rng=rng)
        return [FuelHistoryPoint(product=product, effective_date=date(2026, m, 1), price_zmw=v, price_type=kind) for m, v in ((1, "29.92"), (2, "27.88"))]
    monkeypatch.setattr(router_module, "fetch_fuel_history", hist)
    h = c.get("/api/v1/fuel/history").json()  # defaults
    assert seen == {"product": "PETROL", "kind": "retail", "rng": "1Y"} and h["count"] == 2 and h["observations"][0]["price_zmw"] == "29.92"
    c.get("/api/v1/fuel/history?product=JET_A1&range=ALL&type=wholesale")
    assert seen == {"product": "JET_A1", "kind": "wholesale", "rng": "ALL"}
    for bad in ("product=jet", "range=2Y", "range=1y", "type=pump"):
        assert c.get(f"/api/v1/fuel/history?{bad}").status_code == 422
    def down(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    for name, url in (("fetch_latest_fuel", "/api/v1/fuel/latest"), ("fetch_latest_fuel", "/api/v1/fuel/PETROL"), ("fetch_fuel_history", "/api/v1/fuel/history")):
        monkeypatch.setattr(router_module, name, down)
        r = c.get(url)
        assert r.status_code == 503 and "SECRET" not in r.text


def test_fuel_endpoints_never_call_erb(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("ERB called from an API request")
    monkeypatch.setattr(erb_fuel, "fetch_archive", boom)
    monkeypatch.setattr(router_module, "fetch_latest_fuel", lambda: [])
    monkeypatch.setattr(router_module, "fetch_fuel_history", lambda *a, **k: [])
    c = TestClient(app)
    assert c.get("/api/v1/fuel/latest").json()["missing_products"] == ["PETROL", "DIESEL", "KEROSENE", "JET_A1", "LSGO"]
    assert c.get("/api/v1/fuel/history").json()["observations"] == []


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
        c.execute(text("DROP TABLE IF EXISTS zambia_fuel_prices"))
        c.execute(text((Path(__file__).parents[3] / "db/init/006_zambia_fuel_prices.sql").read_text()))
    yield eng
    eng.dispose()


def rows_of(**kw):
    return parse(**kw)[0]


def test_pg_insert_idempotent_numeric_equivalence_and_no_raw(engine):
    rows = rows_of(retail=RETAIL)
    assert fuel_store.save_fuel_prices(rows, retrieved_at=NOW, source_url="https://www.erb.org.zm/a.zip", engine=engine) == (len(rows), 0)
    assert fuel_store.save_fuel_prices(rows, retrieved_at=NOW, source_url="https://www.erb.org.zm/a.zip", engine=engine) == (0, len(rows))
    same = [erb_fuel.FuelRow(r.effective_date, r.product, r.price_type, r.price_zmw.normalize(), r.unit) for r in rows]  # 31.46 vs 31.460000
    assert fuel_store.save_fuel_prices(same, retrieved_at=NOW, source_url="u", engine=engine) == (0, len(rows))
    with engine.connect() as c:
        stored = c.execute(text("SELECT * FROM zambia_fuel_prices")).mappings().all()
    assert len(stored) == len(rows) and not set(stored[0]) & {"payload", "raw", "raw_payload", "workbook"}
    assert stored[0]["source"] == "erb" and stored[0]["retrieved_at"] == NOW and str(next(r for r in stored if r["price_zmw"] == Decimal("31.46"))["price_zmw"]) == "31.46"


def test_pg_conflict_rolls_back_whole_batch(engine):
    rows = rows_of(retail=RETAIL)
    fuel_store.save_fuel_prices(rows, retrieved_at=NOW, source_url="u", engine=engine)
    with engine.begin() as c:
        c.execute(text("DELETE FROM zambia_fuel_prices WHERE product = 'DIESEL' AND price_type = 'retail'"))
        c.execute(text("UPDATE zambia_fuel_prices SET price_zmw = 1.00 WHERE product = 'PETROL' AND effective_date = '2026-10-01'"))
    with pytest.raises(fuel_store.FuelConflictError) as exc:
        fuel_store.save_fuel_prices(rows, retrieved_at=NOW, source_url="u", engine=engine)
    assert exc.value.count == 1 and "31.46" not in str(exc.value)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM zambia_fuel_prices WHERE product = 'DIESEL'")).scalar() == 0  # not re-inserted


def test_pg_latest_history_filters_order_and_no_interpolation(engine):
    rows = rows_of(retail=[(2026, "October", 31.46, 33.27, 29.76, 31.85, 33.27), (2025, "January", 34.67, 32.43, 28.67, 31.49, 32.43),
                           (2012, "January", 8.16, 7.57, 5.15, "-", "-"), (2024, "June", 25.0, 24.0, 23.0, 22.0, 21.0)],
                    wholesale=[(2026, "October", 23.13, 26.51, 26.29, 25.9, None, None, None, 26.51)])
    fuel_store.save_fuel_prices(rows, retrieved_at=NOW, source_url="u", engine=engine)
    latest = fuel_store.fetch_latest_fuel(engine)
    assert [p.product for p in latest] == ["PETROL", "DIESEL", "KEROSENE", "JET_A1", "LSGO"]
    assert all(p.effective_date == date(2026, 10, 1) and p.price_type == "retail" for p in latest)  # wholesale never leaks into latest
    assert next(p for p in latest if p.product == "PETROL").price_zmw == Decimal("31.46")
    today = date(2026, 11, 20)
    allp = fuel_store.fetch_fuel_history("PETROL", "retail", "ALL", today=today, engine=engine)
    assert [p.effective_date for p in allp] == [date(2012, 1, 1), date(2024, 6, 1), date(2025, 1, 1), date(2026, 10, 1)]  # ascending, only real months
    assert [p.effective_date for p in fuel_store.fetch_fuel_history("PETROL", "retail", "3Y", today=today, engine=engine)] == [date(2024, 6, 1), date(2025, 1, 1), date(2026, 10, 1)]
    assert [p.effective_date for p in fuel_store.fetch_fuel_history("PETROL", "retail", "1Y", today=today, engine=engine)] == [date(2026, 10, 1)]
    assert [p.effective_date for p in fuel_store.fetch_fuel_history("PETROL", "retail", "5Y", today=today, engine=engine)] == [date(2024, 6, 1), date(2025, 1, 1), date(2026, 10, 1)]
    assert len(fuel_store.fetch_fuel_history("PETROL", "wholesale", "ALL", today=today, engine=engine)) == 1
    assert fuel_store.fetch_fuel_history("JET_A1", "retail", "ALL", today=today, engine=engine)[0].effective_date == date(2024, 6, 1)  # 2012 had no Jet A-1 ("-")


def test_pg_job_atomic_write_and_idempotent_rerun(engine):
    xlsx = workbook(retail=RETAIL)
    first = run_job(date(2012, 1, 1), date(2026, 10, 31), xlsx, engine=engine)
    assert first["inserted"] == 13 and first["already_present"] == 0 and first["missing_cells"] == 2
    again = run_job(date(2012, 1, 1), date(2026, 10, 31), xlsx, engine=engine)
    assert again["inserted"] == 0 and again["already_present"] == 13


# ---------------- duplicate ambiguity is decided before observations are discarded ----------------
JAN = date(2022, 1, 1)


def wholesale_book(petrol_cells, *, other=21.0):
    """Wholesale sheet with one row per entry in `petrol_cells`: (value, number_format) for PETROL."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Wholesale Prices"
    ws.append(WHOLE_HEAD)
    for value, fmt in petrol_cells:
        ws.append([2022, "January", value, other, 18.5, 23.7, None, None, None, other])
        ws.cell(row=ws.max_row, column=3).number_format = fmt
        for col in (4, 5, 6, 10):
            ws.cell(row=ws.max_row, column=col).number_format = "0.00"
    rt = wb.create_sheet("Retail Prices ")
    rt.append(RETAIL_HEAD)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def wholesale_petrol(cells):
    rows, stats = parse_workbook(wholesale_book(cells))
    return by(rows, "wholesale", "PETROL"), rows, stats


def test_valid_duplicate_plus_hidden_precision_duplicate_omits_the_month():
    got, rows, stats = wholesale_petrol([(20.0, "0.00"), (22.00448, "0.00")])
    assert JAN not in got  # the surviving 20.00 must not look unambiguous
    assert stats.hidden_precision_cells == 1 and stats.ambiguous_duplicate_cells == 1
    assert by(rows, "wholesale", "DIESEL")[JAN].price_zmw == Decimal("21.00")  # other products unaffected


@pytest.mark.parametrize("bad", ["n/a", -3.0, 0])
def test_valid_duplicate_plus_invalid_numeric_duplicate_omits_the_month(bad):
    got, _, stats = wholesale_petrol([(20.0, "0.00"), (bad, "0.00")])
    assert JAN not in got and stats.invalid_value_cells == 1 and stats.ambiguous_duplicate_cells == 1


def test_identical_valid_duplicates_collapse_to_one_observation():
    got, _, stats = wholesale_petrol([(20.0, "0.00"), (20.0, "0.00"), (19.999999999999996, "0.00")])
    assert got[JAN].price_zmw == Decimal("20.00") and stats.identical_duplicate_cells == 5 and stats.ambiguous_duplicate_cells == 0  # PETROL plus the 4 other products, whose rows repeat


def test_different_valid_duplicates_are_all_omitted():
    got, _, stats = wholesale_petrol([(20.0, "0.00"), (22.0, "0.00")])
    assert JAN not in got and stats.ambiguous_duplicate_cells == 1 and stats.hidden_precision_cells == 0


def test_valid_value_next_to_missing_dash_is_preserved():
    for cells in ([(20.0, "0.00"), ("-", "General")], [("-", "General"), (20.0, "0.00")], [(20.0, "0.00"), (None, "General")]):
        got, _, stats = wholesale_petrol(cells)
        assert got[JAN].price_zmw == Decimal("20.00") and stats.missing_cells >= 1 and stats.ambiguous_duplicate_cells == 0


def test_single_hidden_precision_cell_is_counted_but_not_ambiguous():
    got, _, stats = wholesale_petrol([(20.00448, "0.00")])
    assert JAN not in got and stats.hidden_precision_cells == 1 and stats.ambiguous_duplicate_cells == 0


@pytest.mark.parametrize("cells", [
    [(20.0, "0.00"), (22.00448, "0.00")], [(20.0, "0.00"), (22.0, "0.00")], [(20.0, "0.00"), (20.0, "0.00")],
    [(20.0, "0.00"), ("-", "General")], [(20.0, "0.00"), ("n/a", "0.00"), (20.0, "0.00")],
])
def test_source_row_order_never_changes_the_result(cells):
    forward = wholesale_petrol(cells)
    backward = wholesale_petrol(list(reversed(cells)))
    assert forward[0] == backward[0] and forward[2] == backward[2]


def test_wholesale_general_precision_is_preserved_only_where_presented():
    got, _, stats = wholesale_petrol([(20.00448, "General")])
    assert got[JAN].price_zmw == Decimal("20.00448") and stats.hidden_precision_cells == 0
    got, _, stats = wholesale_petrol([(20.977439999999998, "General"), (20.97744, "General")])
    assert got[JAN].price_zmw == Decimal("20.97744") and stats.identical_duplicate_cells == 5  # identical after 15-digit display
    got, _, stats = wholesale_petrol([(20.00448, "General"), (20.00448, "0.00")])  # same number, hidden behind 2dp in one row
    assert JAN not in got and stats.hidden_precision_cells == 1 and stats.ambiguous_duplicate_cells == 1
    got, _, _ = wholesale_petrol([(33.059999999999995, "0.00")])
    assert got[JAN].price_zmw == Decimal("33.06")  # float noise inside the tolerance still normalizes to 2dp


def test_retail_duplicate_rules_match():
    rows, stats = parse(retail=[(2014, "April", 9.91, 9.2, 6.83, "-", "-"), (2014, "April", 10.63, 10.01, 6.83, "-", "-")])
    apr = date(2014, 4, 1)
    assert apr not in by(rows, "retail", "PETROL") and apr not in by(rows, "retail", "DIESEL")  # mid-month changes: omitted
    assert by(rows, "retail", "KEROSENE")[apr].price_zmw == Decimal("6.83")  # unchanged price in both rows: kept once
    assert stats.ambiguous_duplicate_cells == 2 and stats.identical_duplicate_cells == 1
    rows, stats = parse(retail=[(2014, "May", 9.91, 9.2, 6.83, "-", "-"), (2014, "May", 9.9111, 9.2, 6.83, "-", "-")])
    assert date(2014, 5, 1) not in by(rows, "retail", "PETROL") and stats.hidden_precision_cells == 1 and stats.ambiguous_duplicate_cells == 1
