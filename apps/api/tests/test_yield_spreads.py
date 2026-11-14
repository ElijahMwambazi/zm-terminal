import importlib
import inspect
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.yield_curve import YieldCurve
from app.schemas.yield_spreads import SPREAD_DEFINITIONS, SPREAD_METHODOLOGY, SPREAD_METRIC_TYPE, SPREAD_NOTICE
from app.services import yield_spreads
from app.services.yield_curve import derive_yield_curve, fetch_yield_curve
from app.services.yield_spreads import derive_yield_spreads

router_module = importlib.import_module("app.api.router")
BILL_DAYS = {"91D": 91, "182D": 182, "273D": 273, "364D": 364}
BOND_YEARS = {"2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7, "10Y": 10, "15Y": 15}


def row(tenor, auction, yield_, tender=None):
    if tenor in BILL_DAYS:
        return {"tenor_days": BILL_DAYS[tenor], "auction_date": date.fromisoformat(auction), "tender_number": tender or "22/2026",
                "isin": f"ZM3-{tenor}", "discount_rate": Decimal("1.0000"), "yield_rate": Decimal(yield_), "source_url": "https://boz.example/tb"}
    return {"tenor_years": BOND_YEARS[tenor], "auction_date": date.fromisoformat(auction), "tender_number": tender or "07/2026/BA",
            "isin": f"ZM1-{tenor}", "coupon_rate": Decimal("99.0000"), "yield_rate": Decimal(yield_), "source_url": "https://boz.example/bd"}


def curve(spec, as_of="2026-11-14") -> YieldCurve:
    """spec: {tenor: (auction_date, yield)}"""
    bills = [row(t, d, y) for t, (d, y) in spec.items() if t in BILL_DAYS]
    bonds = [row(t, d, y) for t, (d, y) in spec.items() if t in BOND_YEARS]
    return derive_yield_curve(bills, bonds, as_of=date.fromisoformat(as_of))


REAL = {"91D": ("2026-10-29", "8.5000"), "364D": ("2026-10-29", "10.0000"), "2Y": ("2026-09-25", "12.0000"),
        "10Y": ("2026-09-25", "14.5000"), "15Y": ("2026-09-25", "15.4500")}


def spreads(spec, as_of="2026-11-14"):
    result = derive_yield_spreads(curve(spec, as_of))
    return result, {s.spread_id: s for s in result.available_spreads}


# ---------------- definitions and arithmetic ----------------
def test_the_four_fixed_spread_definitions_are_long_minus_short():
    assert [(i, s, l) for i, s, l in SPREAD_DEFINITIONS] == [
        ("10Y_364D", "364D", "10Y"), ("15Y_364D", "364D", "15Y"), ("10Y_2Y", "2Y", "10Y"), ("15Y_91D", "91D", "15Y")]


def test_spreads_on_the_real_curve_values_in_basis_points():
    result, got = spreads(REAL)
    assert [s.spread_id for s in result.available_spreads] == ["10Y_364D", "15Y_364D", "10Y_2Y", "15Y_91D"]
    assert {k: v.spread_bps for k, v in got.items()} == {
        "10Y_364D": Decimal("450"), "15Y_364D": Decimal("545"), "10Y_2Y": Decimal("250"), "15Y_91D": Decimal("695")}
    assert result.missing_spreads == []


def test_endpoint_yields_and_tenors_are_carried_exactly():
    _, got = spreads(REAL)
    s = got["10Y_364D"]
    assert (s.short_tenor, s.long_tenor, s.short_yield_rate, s.long_yield_rate) == ("364D", "10Y", Decimal("10.0000"), Decimal("14.5000"))
    assert (s.short_auction_date, s.long_auction_date) == (date(2026, 10, 29), date(2026, 9, 25))


def test_decimal_arithmetic_is_exact_never_float_or_rounded():
    _, got = spreads({"364D": ("2026-10-29", "10.1234"), "10Y": ("2026-10-29", "14.5678")})
    assert got["10Y_364D"].spread_bps == Decimal("444.4400")
    assert isinstance(got["10Y_364D"].spread_bps, Decimal)
    _, odd = spreads({"2Y": ("2026-10-29", "0.0001"), "10Y": ("2026-10-29", "0.0003")})
    assert odd["10Y_2Y"].spread_bps == Decimal("0.0200")  # sub-basis-point result is not rounded away
    source = inspect.getsource(yield_spreads)
    assert "float(" not in source and "round(" not in source


def test_negative_spread_is_valid_and_unlabelled():
    result, got = spreads({"364D": ("2026-10-29", "15.00"), "10Y": ("2026-10-29", "14.50")})
    assert got["10Y_364D"].spread_bps == Decimal("-50")
    dumped = result.model_dump_json().lower()
    assert not any(word in dumped for word in ("invert", "steep", "flat", "normal", "bullish", "bearish", "recession"))


def test_zero_spread_is_valid():
    _, got = spreads({"364D": ("2026-10-29", "12.5000"), "10Y": ("2026-10-29", "12.5000")})
    assert got["10Y_364D"].spread_bps == 0


# ---------------- vintage ----------------
def test_same_vintage_spread_has_zero_gap():
    _, got = spreads({"364D": ("2026-10-29", "10"), "10Y": ("2026-10-29", "14")})
    assert got["10Y_364D"].same_vintage is True and got["10Y_364D"].vintage_gap_days == 0


def test_mixed_vintage_spread_reports_the_exact_gap_in_either_direction():
    _, got = spreads(REAL)
    assert (got["10Y_364D"].same_vintage, got["10Y_364D"].vintage_gap_days) == (False, 34)  # 2026-09-25 vs 2026-10-29
    assert (got["10Y_2Y"].same_vintage, got["10Y_2Y"].vintage_gap_days) == (True, 0)
    _, older_long = spreads({"364D": ("2026-10-29", "10"), "10Y": ("2026-02-13", "14")})
    assert older_long["10Y_364D"].vintage_gap_days == 258 and older_long["10Y_364D"].same_vintage is False


# ---------------- missing endpoints ----------------
def test_missing_short_or_long_endpoint_makes_the_spread_unavailable_and_nothing_is_substituted():
    result, got = spreads({"91D": ("2026-10-29", "8.5"), "3Y": ("2026-09-25", "12.59"), "10Y": ("2026-09-25", "14.5"), "15Y": ("2026-09-25", "15.45"),
                           "364D": ("2026-10-29", "10")})  # 2Y missing (3Y present and must not stand in)
    assert "10Y_2Y" not in got
    missing = {m.spread_id: m for m in result.missing_spreads}
    assert list(missing) == ["10Y_2Y"] and missing["10Y_2Y"].missing_tenors == ["2Y"]
    assert set(got) == {"10Y_364D", "15Y_364D", "15Y_91D"}
    result2, got2 = spreads({"364D": ("2026-10-29", "10"), "2Y": ("2026-09-25", "12"), "15Y": ("2026-09-25", "15.45")})  # 10Y and 91D missing
    missing2 = {m.spread_id: m.missing_tenors for m in result2.missing_spreads}
    assert missing2 == {"10Y_364D": ["10Y"], "10Y_2Y": ["10Y"], "15Y_91D": ["91D"]} and set(got2) == {"15Y_364D"}


def test_both_endpoints_missing_are_both_listed_and_no_value_is_created():
    result, got = spreads({"182D": ("2026-10-29", "9"), "5Y": ("2026-02-13", "14.98")})
    assert got == {} and [m.spread_id for m in result.missing_spreads] == ["10Y_364D", "15Y_364D", "10Y_2Y", "15Y_91D"]
    assert result.missing_spreads[0].missing_tenors == ["364D", "10Y"]


# ---------------- classification and metadata ----------------
def test_classification_and_text():
    result, got = spreads(REAL)
    assert (result.classification, result.derived, result.metric_type, result.curve_type) == (
        "derived", True, SPREAD_METRIC_TYPE, "published_auction_yield_tenor_curve")
    assert result.methodology == SPREAD_METHODOLOGY and result.notice == SPREAD_NOTICE
    assert "different BoZ auction dates" in result.notice and "not necessarily a same-date market spread" in result.notice
    assert (result.spread_unit, result.yield_unit) == ("basis_points", "percent_per_annum_as_published")
    assert result.as_of == date(2026, 11, 14)
    assert all(s.classification == "derived" and s.derived is True and s.metric_type == "published_tenor_yield_spread" for s in got.values())


def test_the_service_is_pure_arithmetic_over_the_snapshot():
    source = inspect.getsource(yield_spreads)
    for forbidden in ("sqlalchemy", "httpx", "get_engine", "INSERT", "UPDATE", "CREATE TABLE", "openbb"):
        assert forbidden.lower() not in source.lower()
    c = curve(REAL)
    before = c.model_dump_json()
    derive_yield_spreads(c)
    assert c.model_dump_json() == before


def test_spreads_use_curve_yield_rate_not_discount_or_coupon():
    _, got = spreads({"364D": ("2026-10-29", "10.0000"), "10Y": ("2026-09-25", "14.5000")})
    assert got["10Y_364D"].spread_bps == Decimal("450")  # discount 1.0000 and coupon 99.0000 would give a very different value


# ---------------- API (offline) ----------------
client = TestClient(app)


def sample(as_of="2026-11-14"):
    return curve(REAL, as_of)


def test_latest_route_uses_lusaka_today_and_the_curve_service(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: seen.append(as_of) or sample())
    body = client.get("/api/v1/yield-curve/spreads/latest").json()
    assert seen == [date(2026, 11, 14)] and body["as_of"] == "2026-11-14"
    assert [s["spread_id"] for s in body["available_spreads"]] == ["10Y_364D", "15Y_364D", "10Y_2Y", "15Y_91D"]
    assert [s["spread_bps"] for s in body["available_spreads"]] == ["450.0000", "545.0000", "250.0000", "695.0000"]
    assert body["missing_spreads"] == [] and body["classification"] == "derived" and body["metric_type"] == SPREAD_METRIC_TYPE


def test_as_of_route_passes_the_requested_date_and_decimals_serialize_as_strings(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: seen.append(as_of) or sample(as_of.isoformat()))
    body = client.get("/api/v1/yield-curve/spreads/as-of?date=2026-11-01").json()
    assert seen == [date(2026, 11, 1)] and body["as_of"] == "2026-11-01"
    first = body["available_spreads"][0]
    assert all(isinstance(first[k], str) for k in ("short_yield_rate", "long_yield_rate", "spread_bps"))
    assert (first["same_vintage"], first["vintage_gap_days"]) == (False, 34)


def test_spreads_endpoint_matches_the_curve_endpoint_for_the_same_date(monkeypatch):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample(as_of.isoformat()))
    curve_body = client.get("/api/v1/yield-curve/as-of?date=2026-11-01").json()
    spread_body = client.get("/api/v1/yield-curve/spreads/as-of?date=2026-11-01").json()
    points = {p["tenor"]: p for p in curve_body["points"]}
    assert spread_body["as_of"] == curve_body["as_of"]
    for s in spread_body["available_spreads"]:
        short, long = points[s["short_tenor"]], points[s["long_tenor"]]
        assert (s["short_yield_rate"], s["long_yield_rate"]) == (short["yield_rate"], long["yield_rate"])
        assert (s["short_auction_date"], s["long_auction_date"]) == (short["auction_date"], long["auction_date"])
        assert Decimal(s["spread_bps"]) == (Decimal(long["yield_rate"]) - Decimal(short["yield_rate"])) * 100
    assert len(spread_body["available_spreads"]) + len(spread_body["missing_spreads"]) == 4


def test_route_reports_missing_spreads(monkeypatch):
    partial = curve({"364D": ("2026-10-29", "10"), "15Y": ("2026-09-25", "15.45")})
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: partial)
    body = client.get("/api/v1/yield-curve/spreads/latest").json()
    assert [s["spread_id"] for s in body["available_spreads"]] == ["15Y_364D"]
    assert {m["spread_id"]: m["missing_tenors"] for m in body["missing_spreads"]} == {"10Y_364D": ["10Y"], "10Y_2Y": ["2Y", "10Y"], "15Y_91D": ["91D"]}


@pytest.mark.parametrize("value", ["", "2026-11-1", "20261112", "2026-02-30", "2026-13-01", "abc", "2026-11-12 "])
def test_as_of_requires_strict_dates(monkeypatch, value):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/spreads/as-of", params={"date": value}).status_code == 422


def test_missing_and_future_dates_are_422(monkeypatch):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/spreads/as-of").status_code == 422
    assert client.get("/api/v1/yield-curve/spreads/as-of?date=2026-11-15").status_code == 422
    assert client.get("/api/v1/yield-curve/spreads/as-of?date=2026-11-14").status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/yield-curve/spreads/latest", "/api/v1/yield-curve/spreads/as-of?date=2025-01-01"])
def test_no_curve_is_a_404(monkeypatch, path):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: None)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/yield-curve/spreads/latest", "/api/v1/yield-curve/spreads/as-of?date=2026-10-01"])
def test_database_failure_is_a_safe_503(monkeypatch, path):
    def boom(as_of):
        raise RuntimeError("postgresql://u:SECRET@h/db 8.5000")
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", boom)
    response = client.get(path)
    assert response.status_code == 503 and "SECRET" not in response.text


def test_spreads_never_reach_an_upstream_source_and_existing_curve_routes_still_work(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("spreads must read PostgreSQL only")
    monkeypatch.setattr(router_module, "get_boz_tbills", no_network)
    monkeypatch.setattr(router_module, "get_boz_bonds", no_network)
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/spreads/latest").status_code == 200
    assert client.get("/api/v1/yield-curve/latest").status_code == 200
    assert client.get("/api/v1/yield-curve/as-of?date=2026-11-01").status_code == 200


# ---------------- real PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    init = Path(__file__).parents[3] / "db/init"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS treasury_bill_auctions"))
        c.execute(text("DROP TABLE IF EXISTS government_bond_auctions"))
        for name in ("003_treasury_bill_auctions.sql", "004_government_bond_auctions.sql", "005_fixed_income_history_indexes.sql"):
            c.execute(text((init / name).read_text()))
    yield eng
    eng.dispose()


def test_pg_spreads_come_from_the_stored_curve_and_persist_nothing(engine):
    with engine.begin() as c:
        for tenor, auction, y in (("364D", "2026-10-29", "10.0000"), ("91D", "2026-10-29", "8.5000")):
            r = row(tenor, auction, y)
            c.execute(text("INSERT INTO treasury_bill_auctions (auction_date, tender_number, tenor_days, isin, discount_rate, yield_rate, source, source_url, retrieved_at) "
                           "VALUES (:auction_date, :tender_number, :tenor_days, :isin, :discount_rate, :yield_rate, 'boz', :source_url, now())"), r)
        for tenor, auction, y in (("10Y", "2026-09-25", "14.5000"), ("15Y", "2026-09-25", "15.4500")):
            r = row(tenor, auction, y)
            c.execute(text("INSERT INTO government_bond_auctions (auction_date, tender_number, tenor_years, isin, coupon_rate, yield_rate, source, source_url, retrieved_at) "
                           "VALUES (:auction_date, :tender_number, :tenor_years, :isin, :coupon_rate, :yield_rate, 'boz', :source_url, now())"), r)

    def snapshot():
        with engine.connect() as c:
            tables = sorted(r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")))
            counts = [c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in ("treasury_bill_auctions", "government_bond_auctions")]
        return tables, counts

    before = snapshot()
    stored = fetch_yield_curve(date(2026, 11, 14), engine=engine)
    result = derive_yield_spreads(stored)
    got = {s.spread_id: s for s in result.available_spreads}
    assert got["10Y_364D"].spread_bps == Decimal("450") and got["15Y_91D"].spread_bps == Decimal("695")
    assert {m.spread_id for m in result.missing_spreads} == {"10Y_2Y"}
    assert snapshot() == before and not any("spread" in t for t in before[0])
