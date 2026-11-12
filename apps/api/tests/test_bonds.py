import asyncio
import copy
import importlib
import json
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.bonds import GovernmentBondAuction
from app.services import bond_store, boz_bonds
from app.services.boz_bonds import latest_per_tenor, parse_bond_auctions
from app.services.provider_policy import BOZ_BOND_POLICY, BOZ_TBILL_POLICY, BOZ_USD_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "boz_bonds.json").read_text())
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def payload():
    return copy.deepcopy(FIXTURE)


def parsed(mutate=None):
    p = payload()
    if mutate:
        mutate(p)
    return parse_bond_auctions(p, retrieved_at=NOW)


def para(p, pid):
    return [i for i in p["included"] if i["id"] == pid][0]


def row(**kw):
    base = dict(tenor="10Y", tenor_years=10, auction_date=date(2026, 10, 2), tender_number="97/2026/BA",
                isin="ZM9100000048", coupon_rate="16.0000", yield_rate="13.8000", retrieved_at=NOW)
    return GovernmentBondAuction(**{**base, **kw})


# ---- parsing ----
def test_all_supported_tenors_exact_decimals_identity():
    rows, _ = parsed()
    full = {r.tenor: r for r in rows if r.tender_number == "97/2026/BA"}
    assert list(full) == ["2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
    assert full["3Y"].coupon_rate == Decimal("11.4000") and str(full["15Y"].yield_rate) == "14.7500"
    assert full["5Y"].isin == "ZM9100000089" and full["5Y"].auction_date == date(2026, 10, 2)
    assert all(r.source == "boz" and r.retrieved_at == NOW and r.source_url.endswith("/markets-securities/bonds") for r in rows)


def test_partial_auction_is_valid_and_never_invented():
    rows, _ = parsed()
    assert [r.tenor for r in rows if r.tender_number == "98/2026/BA"] == ["2Y", "10Y"]


def test_reopened_bond_same_isin_across_auctions_are_distinct_rows():
    rows, _ = parsed()
    ten = [r for r in rows if r.tenor == "10Y"]
    assert {r.isin for r in ten} == {"ZM9100000048"} and len(ten) == 3
    assert {r.tender_number for r in ten} == {"96/2026/BA", "97/2026/BA", "98/2026/BA"}
    assert {str(r.yield_rate) for r in ten} == {"14.0000", "13.8000", "13.5000"}


def test_auction_date_is_calendar_date_of_published_offset_datetime():
    def mutate(p):  # late evening +02:00 must not shift to the UTC day
        p["data"][2]["attributes"]["field_bond_auction_date"] = "2026-10-02T00:30:00+02:00"
    rows, _ = parsed(mutate)
    assert {r.auction_date for r in rows if r.tender_number == "97/2026/BA"} == {date(2026, 10, 2)}


def test_duplicate_tenor_rejects_whole_tender():
    rows, rejected = parsed()
    assert [r.tender_number for r in rejected] == ["99/2026/BA"] and rejected[0].auction_date == date(2026, 11, 27)
    assert "99/2026/BA" not in {r.tender_number for r in rows}


@pytest.mark.parametrize("mutate", [
    lambda p: p["data"][2]["attributes"].update(title="oops"),
    lambda p: p["data"][2]["attributes"].update(field_bond_auction_date="2026-10-02"),  # no time/offset
    lambda p: p["data"][2]["attributes"].update(field_bond_auction_date="2026-10-02T15:00:00"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_isin="XX123"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_yield_rate="Nil"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_coupon_rate="1e1"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_coupon_rate="0"),
    lambda p: para(p, "b10")["attributes"].pop("field_10_year_yield_rate"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_tenor="7_years"),
    lambda p: p["included"].remove(para(p, "b10")),
])
def test_malformed_record_rejects_only_that_tender(mutate):
    rows, rejected = parsed(mutate)
    assert len(rejected) == 2 and rejected[0].tender_number == "99/2026/BA"
    assert "97/2026/BA" not in {r.tender_number for r in rows}
    assert {r.tender_number for r in rows} == {"98/2026/BA", "96/2026/BA"}


def test_empty_placeholder_tender_is_rejected_not_a_tenor():
    def mutate(p):
        p["data"][2]["relationships"]["field_auction_results"]["data"] = []
    _, rejected = parsed(mutate)
    assert "97/2026/BA" in [r.tender_number for r in rejected]


@pytest.mark.parametrize("bad", [None, [], {"data": []}, {"data": [{}]}, {"data": [], "included": []}])
def test_structurally_malformed_document_raises(bad):
    with pytest.raises(ValueError):
        parse_bond_auctions(bad, retrieved_at=NOW)


# ---- latest selection and fallback ----
def latest(mutate=None):
    rows, rejected = parsed(mutate)
    return {r.tenor: r for r in latest_per_tenor(rows, rejected)}, rejected


def test_latest_per_tenor_picks_newest_auction_that_contains_tenor():
    got, _ = latest(lambda p: p["data"].pop(0))  # no rejected tender
    assert list(got) == ["2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
    assert got["2Y"].tender_number == got["10Y"].tender_number == "98/2026/BA"
    assert got["3Y"].tender_number == "97/2026/BA" and got["5Y"].auction_date == date(2026, 10, 2)


def test_missing_tenor_in_newer_valid_auction_is_not_a_fallback():
    got, rejected = latest(lambda p: p["data"].pop(0))
    assert rejected == []
    assert all(r.newer_rejected_tenders == [] for r in got.values())


def test_newer_rejected_auction_marks_all_older_rows_fallback():
    got, _ = latest()  # 99/2026/BA (newest) rejected
    assert all(r.newer_rejected_tenders == ["99/2026/BA"] for r in got.values())
    assert got["10Y"].tender_number == "98/2026/BA"


def test_older_rejected_auction_does_not_flag_newer_data():
    def mutate(p):  # break the oldest tender instead
        p["data"].pop(0)
        para(p, "a10")["attributes"].pop("field_10_year_isin")
    got, rejected = latest(mutate)
    assert [r.tender_number for r in rejected] == ["96/2026/BA"]
    assert all(r.newer_rejected_tenders == [] for r in got.values())


def test_rejected_between_auctions_flags_only_older_rows():
    def mutate(p):  # 99/2026/BA now sits between 97 (10-02) and 98 (10-30)
        p["data"][0]["attributes"]["field_bond_auction_date"] = "2026-10-16T15:00:00+02:00"
    got, _ = latest(mutate)
    assert got["10Y"].newer_rejected_tenders == [] and got["2Y"].newer_rejected_tenders == []  # from 98
    assert got["3Y"].newer_rejected_tenders == ["99/2026/BA"]  # from 97: the rejected tender may have had 3Y


def test_undated_or_same_dated_rejected_never_claims_fallback():
    got, _ = latest(lambda p: p["data"][0]["attributes"].update(field_bond_auction_date="bad"))
    assert all(r.newer_rejected_tenders == [] for r in got.values())
    got, _ = latest(lambda p: p["data"][0]["attributes"].update(field_bond_auction_date="2026-10-30T09:00:00+02:00"))
    assert got["2Y"].newer_rejected_tenders == []


# ---- policy ----
def test_bond_policy_is_separate_dataset_under_boz():
    p = BOZ_BOND_POLICY
    assert p.provider_id == "boz" and p.dataset_id == "boz_government_bond_auction_results"
    assert len({BOZ_USD_POLICY.dataset_id, BOZ_TBILL_POLICY.dataset_id, p.dataset_id}) == 3
    assert (p.authentication, p.personal_data_expected, p.raw_response_storage) == ("none", False, "disabled")
    assert p.normalized_storage_allowed and p.attribution_required
    assert p.redistribution == p.commercial_use == "review_required"
    assert set(p.required_timestamps) == {"auction_date", "retrieved_at"}
    assert p.dataset_id in PROVIDER_POLICIES


# ---- API ----
@pytest.fixture
def client(monkeypatch):
    async def fake():
        return parse_bond_auctions(payload(), retrieved_at=NOW)
    monkeypatch.setattr(router_module, "get_boz_bonds", fake)
    return TestClient(app)


def test_latest_endpoint(client):
    body = client.get("/api/v1/government-bonds/latest").json()
    assert [t["tenor"] for t in body["tenors"]] == ["2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
    assert body["missing_tenors"] == [] and body["skipped_tenders"] == ["99/2026/BA"]
    ten = [t for t in body["tenors"] if t["tenor"] == "10Y"][0]
    assert ten["yield_rate"] == "13.5000" and ten["coupon_rate"] == "16.0000" and ten["newer_rejected_tenders"] == ["99/2026/BA"]
    assert "stale" not in ten


def test_tenor_endpoint_and_validation(client):
    assert client.get("/api/v1/government-bonds/5Y").json()["isin"] == "ZM9100000089"
    for bad in ("1Y", "10y", "91D"):
        assert client.get(f"/api/v1/government-bonds/{bad}").status_code == 422


def test_missing_tenor_is_404_and_listed(client, monkeypatch):
    async def recent():  # only the 5-most-recent-style window without 5Y
        rows, rej = parse_bond_auctions(payload(), retrieved_at=NOW)
        return [r for r in rows if r.tenor != "5Y"], rej
    monkeypatch.setattr(router_module, "get_boz_bonds", recent)
    assert client.get("/api/v1/government-bonds/5Y").status_code == 404
    assert client.get("/api/v1/government-bonds/latest").json()["missing_tenors"] == ["5Y"]


@pytest.mark.parametrize("exc,status", [
    (httpx.ReadTimeout("secret-internal-url"), 504),
    (httpx.ConnectError("secret-internal-url"), 502),
    (ValueError("secret-internal-url"), 502),
])
def test_upstream_error_mapping_is_safe(monkeypatch, exc, status):
    async def boom():
        raise exc
    monkeypatch.setattr(router_module, "get_boz_bonds", boom)
    r = TestClient(app).get("/api/v1/government-bonds/latest")
    assert r.status_code == status and "secret" not in r.text and "jsonapi" not in r.text


def test_persistence_failure_still_returns_live_data_and_logs_safely(client, monkeypatch, caplog):
    def fail(rows):
        raise RuntimeError("postgresql://user:SECRETPW@db/zm 13.8000")
    monkeypatch.setattr(router_module, "save_bond_auctions", fail)
    with caplog.at_level(logging.WARNING):
        assert client.get("/api/v1/government-bonds/latest").status_code == 200
    assert "RuntimeError" in caplog.text and "SECRETPW" not in caplog.text and "13.8000" not in caplog.text
    def conflict(rows):
        raise bond_store.BondConflictError("1 row 13.8000")
    monkeypatch.setattr(router_module, "save_bond_auctions", conflict)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert client.get("/api/v1/government-bonds/latest").status_code == 200
    assert "conflict" in caplog.text and "13.8000" not in caplog.text


def test_adapter_request_and_content_type():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=payload(), headers={"content-type": "application/vnd.api+json"})
    async def run(h):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await boz_bonds.get_boz_bonds(client=c)
    rows, rejected = asyncio.run(run(handler))
    assert "page%5Blimit%5D=5" in seen["url"] and "bond_auction" in seen["url"]
    assert len(rows) == 13 and [r.tender_number for r in rejected] == ["99/2026/BA"]
    with pytest.raises(ValueError):
        asyncio.run(run(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})))


# ---- real PostgreSQL (opt-in) ----
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    sql = (Path(__file__).parents[3] / "db/init/004_government_bond_auctions.sql").read_text()
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS government_bond_auctions"))
        c.execute(text(sql))
    yield eng
    eng.dispose()


def test_pg_idempotent_reopened_isin_distinct_rows_no_raw(engine):
    rows, _ = parsed()
    assert bond_store.save_bond_auctions(rows, engine=engine) == (len(rows), 0)
    assert bond_store.save_bond_auctions(rows, engine=engine) == (0, len(rows))
    with engine.connect() as c:
        stored = c.execute(text("SELECT * FROM government_bond_auctions")).mappings().all()
        same_isin = c.execute(text("SELECT count(*) FROM government_bond_auctions WHERE isin='ZM9100000048'")).scalar()
    assert len(stored) == len(rows) and same_isin == 3  # one ISIN, three auctions, three rows
    assert any(str(r["yield_rate"]) == "13.8000" and r["source"] == "boz" and r["retrieved_at"] == NOW for r in stored)
    assert not set(stored[0]) & {"payload", "raw", "raw_payload", "response"}


def test_pg_numeric_equal_noop_and_conflict_rolls_back(engine):
    bond_store.save_bond_auctions([row()], engine=engine)
    assert bond_store.save_bond_auctions([row(yield_rate="13.80", coupon_rate="16")], engine=engine) == (0, 1)
    new = row(tenor="2Y", tenor_years=2, isin="ZM9100000063", yield_rate="11.2500", coupon_rate="11.2500")
    with pytest.raises(bond_store.BondConflictError):
        bond_store.save_bond_auctions([new, row(yield_rate="13.9000")], engine=engine)
    with engine.connect() as c:
        n, y = c.execute(text("SELECT count(*), max(yield_rate) FROM government_bond_auctions")).one()
    assert n == 1 and y == Decimal("13.8000")  # new row rolled back, original untouched
