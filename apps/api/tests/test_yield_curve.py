import importlib
import os
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.yield_curve import (
    CURVE_METHODOLOGY, CURVE_NOTICE, CURVE_TENORS, CURVE_TYPE, GovernmentBondCurvePoint, TreasuryBillCurvePoint, YieldCurve,
)
from app.services import yield_curve
from app.services.yield_curve import derive_yield_curve, fetch_yield_curve

router_module = importlib.import_module("app.api.router")
BILL_URL = "https://www.boz.zm/markets-securities/treasury-bills"
BOND_URL = "https://www.boz.zm/markets-securities/bonds"
BILL_DAYS = {"91D": 91, "182D": 182, "273D": 273, "364D": 364}
BOND_YEARS = {"2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7, "10Y": 10, "15Y": 15}


def bill(tenor, auction, yield_="8.5000", discount="8.0000", tender="22/2026", isin=None):
    return {"tenor_days": BILL_DAYS[tenor], "auction_date": date.fromisoformat(auction), "tender_number": tender,
            "isin": isin or f"ZM3-{tenor}-{tender}", "discount_rate": Decimal(discount), "yield_rate": Decimal(yield_), "source_url": BILL_URL}


def bond(tenor, auction, yield_="14.5000", coupon="16.0000", tender="07/2026/BA", isin=None):
    return {"tenor_years": BOND_YEARS[tenor], "auction_date": date.fromisoformat(auction), "tender_number": tender,
            "isin": isin or f"ZM1-{tenor}", "coupon_rate": Decimal(coupon), "yield_rate": Decimal(yield_), "source_url": BOND_URL}


def curve(bills=(), bonds=(), as_of="2026-11-14"):
    return derive_yield_curve(list(bills), list(bonds), as_of=date.fromisoformat(as_of))


def by_tenor(c: YieldCurve):
    return {p.tenor: p for p in c.points}


# ---------------- selection: newest accepted observation per tenor ----------------
def test_each_tenor_takes_its_own_newest_observation_on_or_before_as_of():
    c = curve(
        bills=[bill("91D", "2026-10-01", "8.1"), bill("91D", "2026-10-29", "8.5"), bill("364D", "2026-09-17", "9.8")],
        bonds=[bond("10Y", "2026-02-13", "14.0"), bond("10Y", "2026-09-25", "14.5"), bond("2Y", "2026-08-28", "12.0")])
    got = by_tenor(c)
    assert (got["91D"].auction_date, got["91D"].yield_rate) == (date(2026, 10, 29), Decimal("8.5"))
    assert (got["364D"].auction_date, got["364D"].yield_rate) == (date(2026, 9, 17), Decimal("9.8"))  # not forced to the newest tender
    assert (got["10Y"].auction_date, got["10Y"].yield_rate) == (date(2026, 9, 25), Decimal("14.5"))
    assert (got["2Y"].auction_date, got["2Y"].yield_rate) == (date(2026, 8, 28), Decimal("12.0"))
    assert c.point_count == len(c.points) == 4


def test_different_auction_dates_across_tenors_are_mixed_vintage_with_span_and_bounds():
    c = curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("5Y", "2026-02-13"), bond("10Y", "2026-09-25")])
    assert c.mixed_vintage is True
    assert (c.oldest_point_date, c.newest_point_date, c.vintage_span_days) == (date(2026, 2, 13), date(2026, 10, 29), 258)
    assert [p.auction_date for p in c.points] == [date(2026, 10, 29), date(2026, 2, 13), date(2026, 9, 25)]  # each keeps its own date


def test_all_points_from_one_auction_date_are_not_mixed_vintage():
    c = curve(bills=[bill("91D", "2026-10-29"), bill("182D", "2026-10-29")], bonds=[bond("2Y", "2026-10-29")])
    assert c.mixed_vintage is False and c.vintage_span_days == 0 and c.oldest_point_date == c.newest_point_date == date(2026, 10, 29)


def test_a_single_point_is_a_valid_one_date_curve():
    c = curve(bills=[bill("273D", "2026-10-29")])
    assert (c.point_count, c.mixed_vintage, c.vintage_span_days) == (1, False, 0)
    assert c.missing_tenors == [t for t in CURVE_TENORS if t != "273D"]


def test_missing_tenors_are_omitted_and_listed_in_order_never_substituted():
    c = curve(bills=[bill("91D", "2026-10-29"), bill("364D", "2026-10-29")], bonds=[bond("2Y", "2026-09-25"), bond("10Y", "2026-09-25")])
    assert [p.tenor for p in c.points] == ["91D", "364D", "2Y", "10Y"]
    assert c.missing_tenors == ["182D", "273D", "3Y", "5Y", "7Y", "15Y"]
    assert c.point_count + len(c.missing_tenors) == len(CURVE_TENORS)


def test_as_of_excludes_observations_after_the_cutoff_and_includes_the_cutoff_day():
    rows = dict(bills=[bill("91D", "2026-10-01", "8.1"), bill("91D", "2026-10-15", "8.3"), bill("91D", "2026-10-29", "8.5")])
    assert by_tenor(curve(**rows, as_of="2026-10-28"))["91D"].yield_rate == Decimal("8.3")      # 29 Oct is after the cutoff
    on_the_day = by_tenor(curve(**rows, as_of="2026-10-29"))["91D"]
    assert on_the_day.yield_rate == Decimal("8.5") and on_the_day.age_days == 0                  # exactly on the auction date is included
    assert by_tenor(curve(**rows, as_of="2026-10-01"))["91D"].yield_rate == Decimal("8.1")
    only_future = curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("2Y", "2026-09-25")], as_of="2026-09-25")
    assert [p.tenor for p in only_future.points] == ["2Y"] and only_future.missing_tenors[0] == "91D"


def test_no_observation_on_or_before_the_date_gives_no_curve():
    assert curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("2Y", "2026-09-25")], as_of="2026-09-24") is None
    assert curve(as_of="2026-11-14") is None


def test_points_follow_the_fixed_tenor_sequence_regardless_of_input_order():
    bills = [bill(t, "2026-10-29") for t in ("364D", "91D", "273D", "182D")]
    bonds = [bond(t, "2026-09-25") for t in ("15Y", "2Y", "7Y", "3Y", "10Y", "5Y")]
    random.Random(3).shuffle(bills); random.Random(4).shuffle(bonds)
    c = curve(bills=bills, bonds=bonds)
    assert [p.tenor for p in c.points] == list(CURVE_TENORS) == ["91D", "182D", "273D", "364D", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
    assert [p.tenor_order for p in c.points] == list(range(1, 11)) and c.missing_tenors == []


def test_age_days_is_a_non_negative_calendar_day_count_since_the_observation():
    c = curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("5Y", "2026-02-13")], as_of="2026-11-14")
    got = by_tenor(c)
    assert (got["91D"].age_days, got["5Y"].age_days) == (16, 274)
    assert all(isinstance(p.age_days, int) and p.age_days >= 0 for p in c.points)
    assert by_tenor(curve(bills=[bill("91D", "2025-12-31")], as_of="2026-01-02"))["91D"].age_days == 2  # across a year boundary


def test_same_day_ties_resolve_deterministically_to_the_higher_tender_number():
    rows = [bill("91D", "2026-10-29", "8.1", tender="21/2026"), bill("91D", "2026-10-29", "8.5", tender="22/2026")]
    assert by_tenor(curve(bills=rows))["91D"].tender_number == "22/2026"
    assert by_tenor(curve(bills=list(reversed(rows))))["91D"].tender_number == "22/2026"


# ---------------- instrument distinctions: yield_rate only ----------------
def test_bill_points_use_yield_rate_and_keep_discount_rate_as_supporting_metadata():
    c = curve(bills=[bill("182D", "2026-10-29", yield_="9.1000", discount="8.7050")])
    (point,) = c.points
    assert isinstance(point, TreasuryBillCurvePoint) and point.instrument_type == "treasury_bill"
    assert point.yield_rate == Decimal("9.1000") and point.discount_rate == Decimal("8.7050") and point.yield_rate != point.discount_rate
    assert not hasattr(point, "coupon_rate")


def test_bond_points_use_yield_rate_and_keep_coupon_rate_as_supporting_metadata():
    c = curve(bonds=[bond("15Y", "2026-09-25", yield_="15.4500", coupon="17.5900")])
    (point,) = c.points
    assert isinstance(point, GovernmentBondCurvePoint) and point.instrument_type == "government_bond"
    assert point.yield_rate == Decimal("15.4500") and point.coupon_rate == Decimal("17.5900") and point.yield_rate != point.coupon_rate
    assert not hasattr(point, "discount_rate")


def test_serialized_points_carry_the_correct_supporting_field_only():
    body = curve(bills=[bill("91D", "2026-10-29", "8.5", "8.3")], bonds=[bond("2Y", "2026-09-25", "12.0", "11.0")]).model_dump(mode="json")
    tb, gb = body["points"]
    assert (tb["yield_rate"], tb["discount_rate"], "coupon_rate" in tb) == ("8.5", "8.3", False)
    assert (gb["yield_rate"], gb["coupon_rate"], "discount_rate" in gb) == ("12.0", "11.0", False)
    assert tb["yield_rate"] != tb["discount_rate"] and gb["yield_rate"] != gb["coupon_rate"]


def test_yields_stay_exact_published_percentages_never_divided_or_floated():
    (point,) = curve(bills=[bill("91D", "2026-10-29", yield_="8.5000")]).points
    assert point.yield_rate == Decimal("8.5000") and str(point.yield_rate) == "8.5000" and isinstance(point.yield_rate, Decimal)
    assert point.yield_unit == "percent_per_annum_as_published"
    assert curve(bills=[bill("91D", "2026-10-29")]).model_dump(mode="json")["points"][0]["yield_rate"] == "8.5000"


# ---------------- no hidden interpolation or maturity inference ----------------
def test_no_point_is_created_for_a_missing_tenor_or_between_adjacent_tenors():
    c = curve(bills=[bill("91D", "2026-10-29"), bill("182D", "2026-10-29"), bill("364D", "2026-10-29")], bonds=[bond("2Y", "2026-09-25")])
    tenors = [p.tenor for p in c.points]
    assert "273D" not in tenors and "273D" in c.missing_tenors  # missing 273D stays missing
    assert tenors == ["91D", "182D", "364D", "2Y"]  # nothing between 364D and 2Y
    assert c.point_count == len(c.points) == 4 and set(tenors) <= set(CURVE_TENORS)
    assert len({p.tenor for p in c.points}) == len(c.points)  # one point per tenor, never duplicates


def test_unsupported_tenor_rows_are_ignored_not_mapped_to_a_neighbour():
    odd_bill = {**bill("91D", "2026-10-29"), "tenor_days": 120}
    odd_bond = {**bond("2Y", "2026-09-25"), "tenor_years": 4}
    assert curve(bills=[odd_bill], bonds=[odd_bond]) is None


def test_the_curve_exposes_no_maturity_concept_anywhere():
    body = curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("10Y", "2026-09-25")]).model_dump(mode="json")
    keys = set(body) | {k for p in body["points"] for k in p}
    assert not [k for k in keys if "matur" in k.lower() or k in {"remaining_maturity", "maturity_date", "years_to_maturity"}]
    assert (body["classification"], body["derived"], body["curve_type"]) == ("derived", True, CURVE_TYPE) and CURVE_TYPE == "published_auction_yield_tenor_curve"
    assert body["methodology"] == CURVE_METHODOLOGY and body["notice"] == CURVE_NOTICE
    assert "latest stored accepted" in CURVE_METHODOLOGY.lower() and "not calculated remaining maturities" in CURVE_NOTICE
    assert "official" not in CURVE_METHODOLOGY.lower() and body["as_of"] == "2026-11-14"


# ---------------- reopened bonds ----------------
REOPENED = "ZM1000007642"


def reopened():
    """BoZ reopens an existing bond: same ISIN (and maturity), new auction, new yield, same coupon, same tenor bucket."""
    return [bond("10Y", "2026-02-13", "14.0000", "16.6000", "02/2026/BA", REOPENED), bond("10Y", "2026-05-29", "14.2000", "16.6000", "05/2026/BA", REOPENED),
            bond("10Y", "2026-09-25", "14.5000", "16.6000", "07/2026/BA", REOPENED)]


def test_reopened_bond_latest_accepted_auction_on_or_before_as_of_wins():
    assert by_tenor(curve(bonds=reopened()))["10Y"].yield_rate == Decimal("14.5000")
    middle = by_tenor(curve(bonds=reopened(), as_of="2026-07-01"))["10Y"]
    assert (middle.auction_date, middle.yield_rate, middle.tender_number) == (date(2026, 5, 29), Decimal("14.2000"), "05/2026/BA")
    assert by_tenor(curve(bonds=reopened(), as_of="2026-02-13"))["10Y"].yield_rate == Decimal("14.0000")
    assert curve(bonds=reopened(), as_of="2026-02-12") is None


def test_reopened_bond_keeps_its_stored_tenor_and_isin_and_derives_no_maturity():
    (point,) = curve(bonds=reopened()).points
    assert (point.tenor, point.isin, point.coupon_rate) == ("10Y", REOPENED, Decimal("16.6000"))
    assert "matur" not in " ".join(point.model_dump(mode="json")).lower()
    assert len({r["isin"] for r in reopened()}) == 1 and len(reopened()) == 3  # the same ISIN across auctions is normal


def test_same_isin_in_different_tenor_buckets_is_not_merged_or_reassigned():
    rows = [bond("7Y", "2026-09-25", isin="ZM-SHARED"), bond("10Y", "2026-09-25", isin="ZM-SHARED")]
    assert [(p.tenor, p.isin) for p in curve(bonds=rows).points] == [("7Y", "ZM-SHARED"), ("10Y", "ZM-SHARED")]


# ---------------- API (offline) ----------------
client = TestClient(app)


def sample(as_of="2026-11-14"):
    return curve(bills=[bill("91D", "2026-10-29")], bonds=[bond("5Y", "2026-02-13"), bond("10Y", "2026-09-25")], as_of=as_of)


def test_latest_uses_lusaka_today_and_returns_the_snapshot(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: seen.append(as_of) or sample())
    body = client.get("/api/v1/yield-curve/latest").json()
    assert seen == [date(2026, 11, 14)] and body["as_of"] == "2026-11-14"
    assert (body["classification"], body["curve_type"], body["mixed_vintage"], body["point_count"], body["vintage_span_days"]) == ("derived", CURVE_TYPE, True, 3, 258)
    assert body["missing_tenors"] == ["182D", "273D", "364D", "2Y", "3Y", "7Y", "15Y"]
    assert [p["tenor"] for p in body["points"]] == ["91D", "5Y", "10Y"] and body["points"][1]["age_days"] == 274
    assert body["points"][0]["instrument_type"] == "treasury_bill" and body["points"][1]["instrument_type"] == "government_bond"


def test_as_of_passes_the_requested_date(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: seen.append(as_of) or sample(as_of.isoformat()))
    body = client.get("/api/v1/yield-curve/as-of?date=2026-10-01").json()
    assert seen == [date(2026, 10, 1)] and body["as_of"] == "2026-10-01"
    assert client.get("/api/v1/yield-curve/as-of?date=2026-11-14").status_code == 200  # today is allowed


@pytest.mark.parametrize("value", ["", "2026-11-1", "20261112", "2026-02-30", "2026-13-01", "2026-11-12T00:00:00", "12/11/2026", "abc", "2026-11-12 ", "２０２６-11-12"])
def test_as_of_requires_strict_yyyy_mm_dd(monkeypatch, value):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/as-of", params={"date": value}).status_code == 422


def test_as_of_date_is_required_and_future_dates_are_rejected(monkeypatch):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/as-of").status_code == 422
    future = client.get("/api/v1/yield-curve/as-of?date=2026-11-15")
    assert future.status_code == 422 and "future" in future.json()["detail"]


@pytest.mark.parametrize("path", ["/api/v1/yield-curve/latest", "/api/v1/yield-curve/as-of?date=2025-01-01"])
def test_no_observation_before_the_date_is_a_404(monkeypatch, path):
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: None)
    response = client.get(path)
    assert response.status_code == 404 and "No stored accepted BoZ auction observation" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/v1/yield-curve/latest", "/api/v1/yield-curve/as-of?date=2026-10-01"])
def test_database_failure_is_a_safe_503(monkeypatch, path):
    def boom(as_of):
        raise RuntimeError("postgresql://u:SECRET@h/db 8.5000")
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", boom)
    response = client.get(path)
    assert response.status_code == 503 and "SECRET" not in response.text and "8.5000" not in response.text


def test_the_curve_never_reaches_an_upstream_source(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("the yield curve must read PostgreSQL only")
    monkeypatch.setattr(router_module, "get_boz_tbills", no_network)
    monkeypatch.setattr(router_module, "get_boz_bonds", no_network)
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_yield_curve", lambda as_of: sample())
    assert client.get("/api/v1/yield-curve/latest").status_code == 200


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


def put(engine, bills=(), bonds=()):
    with engine.begin() as c:
        for r in bills:
            c.execute(text("INSERT INTO treasury_bill_auctions (auction_date, tender_number, tenor_days, isin, discount_rate, yield_rate, source, source_url, retrieved_at) "
                           "VALUES (:auction_date, :tender_number, :tenor_days, :isin, :discount_rate, :yield_rate, 'boz', :source_url, now())"), r)
        for r in bonds:
            c.execute(text("INSERT INTO government_bond_auctions (auction_date, tender_number, tenor_years, isin, coupon_rate, yield_rate, source, source_url, retrieved_at) "
                           "VALUES (:auction_date, :tender_number, :tenor_years, :isin, :coupon_rate, :yield_rate, 'boz', :source_url, now())"), r)


def test_pg_snapshot_selects_newest_per_tenor_from_both_tables_with_exact_decimals(engine):
    put(engine,
        bills=[bill(t, d, y, disc) for t, d, y, disc in [("91D", "2026-10-01", "8.1000", "7.9"), ("91D", "2026-10-29", "8.5000", "8.3236"),
                                                       ("182D", "2026-10-29", "9.1000", "8.7050"), ("364D", "2026-10-29", "10.0000", "9.0932")]],
        bonds=[bond("10Y", "2026-02-13", "14.0000", "16.6"), bond("10Y", "2026-09-25", "14.5000", "16.6000"), bond("5Y", "2026-02-13", "14.9800", "14.98")])
    c = fetch_yield_curve(date(2026, 11, 14), engine=engine)
    got = by_tenor(c)
    assert [p.tenor for p in c.points] == ["91D", "182D", "364D", "5Y", "10Y"]
    assert (str(got["91D"].yield_rate), str(got["91D"].discount_rate)) == ("8.5000", "8.3236")   # yield_rate, not discount_rate
    assert (str(got["10Y"].yield_rate), str(got["10Y"].coupon_rate)) == ("14.5000", "16.6000")    # yield_rate, not coupon_rate
    assert c.mixed_vintage is True and (c.oldest_point_date, c.newest_point_date) == (date(2026, 2, 13), date(2026, 10, 29))
    assert c.missing_tenors == ["273D", "2Y", "3Y", "7Y", "15Y"] and got["91D"].source_url == BILL_URL and got["10Y"].source_url == BOND_URL


def test_pg_as_of_filters_in_the_database_and_includes_the_cutoff_day(engine):
    put(engine, bills=[bill("91D", "2026-10-01", "8.1"), bill("91D", "2026-10-29", "8.5")], bonds=[bond("2Y", "2026-09-25", "12.0")])
    assert by_tenor(fetch_yield_curve(date(2026, 10, 28), engine=engine))["91D"].yield_rate == Decimal("8.1")
    assert by_tenor(fetch_yield_curve(date(2026, 10, 29), engine=engine))["91D"].yield_rate == Decimal("8.5")
    assert [p.tenor for p in fetch_yield_curve(date(2026, 9, 30), engine=engine).points] == ["2Y"]
    assert fetch_yield_curve(date(2026, 9, 24), engine=engine) is None


def test_pg_reopened_bond_isin_across_stored_auctions(engine):
    put(engine, bonds=reopened())
    assert fetch_yield_curve(date(2026, 11, 14), engine=engine).points[0].auction_date == date(2026, 9, 25)
    (middle,) = fetch_yield_curve(date(2026, 7, 1), engine=engine).points
    assert (middle.tenor, middle.isin, str(middle.yield_rate), middle.auction_date) == ("10Y", REOPENED, "14.2000", date(2026, 5, 29))


def test_pg_curve_is_read_only_and_persists_nothing(engine):
    put(engine, bills=[bill("91D", "2026-10-29")], bonds=[bond("2Y", "2026-09-25")])
    with engine.connect() as c:
        before = (c.execute(text("SELECT count(*) FROM treasury_bill_auctions")).scalar(), c.execute(text("SELECT count(*) FROM government_bond_auctions")).scalar())
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    fetch_yield_curve(date(2026, 11, 14), engine=engine); fetch_yield_curve(date(2026, 10, 1), engine=engine)
    with engine.connect() as c:
        assert before == (c.execute(text("SELECT count(*) FROM treasury_bill_auctions")).scalar(), c.execute(text("SELECT count(*) FROM government_bond_auctions")).scalar())
        assert {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))} == tables
    assert not [t for t in tables if "curve" in t] and not list(Path(__file__).parents[3].glob("db/init/010*"))
    assert yield_curve.__doc__ and "no HTTP" in yield_curve.__doc__
