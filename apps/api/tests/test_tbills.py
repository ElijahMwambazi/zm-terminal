import copy
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

from app.main import app
from app.schemas.tbills import TreasuryBillAuction
from app.services import boz_treasury, tbill_store
from app.services.boz_treasury import latest_per_tenor, parse_tbill_auctions
from app.services.provider_policy import BOZ_TBILL_POLICY, BOZ_USD_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "boz_tbills.json").read_text())
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def payload():
    return copy.deepcopy(FIXTURE)


def parsed():
    return parse_tbill_auctions(payload(), retrieved_at=NOW)


def row(**kw):
    base = dict(tenor="364D", tenor_days=364, auction_date=date(2026, 10, 29), tender_number="97/2026",
                isin="ZM9000000045", discount_rate="8.5900", yield_rate="9.4000", retrieved_at=NOW)
    return TreasuryBillAuction(**{**base, **kw})


# ---- parsing ----
def test_all_four_tenors_exact_decimals_and_identity():
    rows, _ = parsed()
    full = {r.tenor: r for r in rows if r.tender_number == "97/2026"}
    assert list(full) == ["91D", "182D", "273D", "364D"]
    assert full["182D"].isin == "ZM9000000029"
    assert full["364D"].yield_rate == Decimal("9.4000") and str(full["364D"].yield_rate) == "9.4000"
    assert str(full["182D"].discount_rate) == "7.9300"
    assert full["273D"].auction_date == date(2026, 10, 29) and full["273D"].tender_number == "97/2026"
    assert all(r.source == "boz" and r.retrieved_at == NOW for r in rows)
    assert full["91D"].source_url.endswith("/markets-securities/treasury-bills")


def test_duplicate_tenor_tender_is_skipped_whole_and_reported():
    rows, skipped = parsed()
    assert [r.tender_number for r in skipped] == ["99/2026"]
    assert skipped[0].auction_date == date(2026, 11, 26)
    assert "99/2026" not in {r.tender_number for r in rows}


def test_partial_tender_is_valid_and_latest_is_per_tenor():
    rows, _ = parsed()
    latest = {r.tenor: r for r in latest_per_tenor(rows, NOW)}
    assert latest["91D"].tender_number == "98/2026" and latest["91D"].yield_rate == Decimal("7.4000")
    assert latest["182D"].tender_number == "97/2026"  # not published in newer tender: not invented
    assert latest["364D"].auction_date == date(2026, 11, 12)


def test_stale_flag_uses_lusaka_date():
    rows, _ = parsed()
    assert not any(r.stale for r in latest_per_tenor(rows, NOW))
    late = datetime(2026, 12, 20, tzinfo=timezone.utc)
    assert all(r.stale for r in latest_per_tenor(rows, late))


@pytest.mark.parametrize("mutate", [
    lambda p: p["data"][2]["attributes"].update(title="oops"),
    lambda p: p["data"][2]["attributes"].update(field_tbills_auction_date="12-11-2026"),
    lambda p: [i for i in p["included"] if i["id"] == "p4"][0]["attributes"].update(field_364_yield_rate="Nil"),
    lambda p: [i for i in p["included"] if i["id"] == "p4"][0]["attributes"].update(field_364_yield_rate="1e1"),
    lambda p: [i for i in p["included"] if i["id"] == "p4"][0]["attributes"].update(field_364_yield_rate="0"),
    lambda p: [i for i in p["included"] if i["id"] == "p4"][0]["attributes"].update(field_364_days_isin="XX123"),
    lambda p: [i for i in p["included"] if i["id"] == "p4"][0]["attributes"].pop("field_364_days_discount_rate"),
    lambda p: p["included"].remove([i for i in p["included"] if i["id"] == "p4"][0]),
])
def test_malformed_record_rejects_only_that_tender(mutate):
    p = payload()
    mutate(p)
    rows, skipped = parse_tbill_auctions(p, retrieved_at=NOW)
    assert len(skipped) == 2 and skipped[0].tender_number == "99/2026"  # bad tender reported (number None if unreadable)
    assert "97/2026" not in {r.tender_number for r in rows}


@pytest.mark.parametrize("bad", [None, [], {"data": []}, {"data": [{}]}, {"data": [], "included": []}])
def test_structurally_malformed_document_raises(bad):
    with pytest.raises(ValueError):
        parse_tbill_auctions(bad, retrieved_at=NOW)


def test_no_valid_tender_raises():
    p = payload()
    p["data"] = p["data"][:1]
    with pytest.raises(ValueError):
        parse_tbill_auctions(p, retrieved_at=NOW)


# ---- fallback semantics (rejected tender vs actual fallback) ----
def latest(mutate=None):
    p = payload()
    if mutate:
        mutate(p)
    rows, rejected = parse_tbill_auctions(p, retrieved_at=NOW)
    return {r.tenor: r for r in latest_per_tenor(rows, NOW, rejected)}, rejected


def test_newer_malformed_tender_marks_every_older_row_as_fallback():
    got, rejected = latest()  # 99/2026 (newest) rejected
    assert [r.tender_number for r in rejected] == ["99/2026"]
    assert all(r.newer_rejected_tenders == ["99/2026"] for r in got.values())
    assert got["364D"].tender_number == "98/2026"  # the value shown is the older valid one


def test_older_malformed_tender_with_newer_valid_is_not_a_fallback():
    got, rejected = latest(lambda p: p["data"][0]["attributes"].update(field_tbills_auction_date="2026-10-01"))
    assert [r.tender_number for r in rejected] == ["99/2026"]  # still reported
    assert all(r.newer_rejected_tenders == [] for r in got.values())


def test_unrelated_rejected_tender_makes_no_false_fallback_claim():
    def mutate(p):  # rejected tender is older than 98/2026 (so older than the 91D/364D rows)
        p["data"][0]["attributes"].update(field_tbills_auction_date="2026-11-05")
    got, _ = latest(mutate)
    # 91D/364D come from 98/2026 (11-12), newer than the rejected 11-05 tender: not fallbacks.
    assert got["91D"].newer_rejected_tenders == [] and got["364D"].newer_rejected_tenders == []
    # 182D/273D come from 97/2026 (10-29): the rejected 11-05 tender could have carried them.
    assert got["182D"].newer_rejected_tenders == ["99/2026"]


def test_undated_or_same_dated_rejected_tender_never_claims_fallback():
    got, _ = latest(lambda p: p["data"][0]["attributes"].update(field_tbills_auction_date="not-a-date"))
    assert all(r.newer_rejected_tenders == [] for r in got.values())
    got, _ = latest(lambda p: p["data"][0]["attributes"].update(field_tbills_auction_date="2026-11-12"))
    assert got["91D"].newer_rejected_tenders == []  # same date as 98/2026: not "newer"


def test_valid_partial_newer_tender_without_rejections_is_not_a_fallback():
    def mutate(p):
        p["data"].pop(0)  # no rejected tender at all
    got, rejected = latest(mutate)
    assert rejected == []
    assert got["364D"].tender_number == "98/2026" and got["182D"].tender_number == "97/2026"
    assert all(r.newer_rejected_tenders == [] for r in got.values())


# ---- policy ----
def test_tbill_policy_is_separate_dataset_under_boz():
    p = BOZ_TBILL_POLICY
    assert p.provider_id == "boz" and p.dataset_id == "boz_treasury_bill_auction_results"
    assert p.dataset_id != BOZ_USD_POLICY.dataset_id
    assert (p.authentication, p.personal_data_expected, p.raw_response_storage) == ("none", False, "disabled")
    assert p.normalized_storage_allowed and p.attribution_required
    assert p.redistribution == p.commercial_use == "review_required"
    assert set(p.required_timestamps) == {"auction_date", "retrieved_at"}
    assert {BOZ_USD_POLICY.dataset_id, p.dataset_id} <= set(PROVIDER_POLICIES)


# ---- API ----
@pytest.fixture
def client(monkeypatch):
    async def fake():
        return parse_tbill_auctions(payload(), retrieved_at=NOW)
    monkeypatch.setattr(router_module, "get_boz_tbills", fake)
    return TestClient(app)


def test_latest_endpoint(client):
    body = client.get("/api/v1/treasury-bills/latest").json()
    assert [t["tenor"] for t in body["tenors"]] == ["91D", "182D", "273D", "364D"]
    assert body["missing_tenors"] == [] and body["skipped_tenders"] == ["99/2026"]
    assert all(t["newer_rejected_tenders"] == ["99/2026"] for t in body["tenors"])
    t364 = body["tenors"][3]
    assert t364["yield_rate"] == "9.3000" and t364["tender_number"] == "98/2026"
    assert t364["source"] == "boz" and "discount_rate" in t364


def test_tenor_endpoint_and_validation(client):
    assert client.get("/api/v1/treasury-bills/182D").json()["isin"] == "ZM9000000029"
    assert client.get("/api/v1/treasury-bills/30D").status_code == 422
    assert client.get("/api/v1/treasury-bills/364d").status_code == 422


def test_missing_tenor_is_404(client, monkeypatch):
    async def only91():
        rows, s = parse_tbill_auctions(payload(), retrieved_at=NOW)
        return [r for r in rows if r.tenor == "91D"], s
    monkeypatch.setattr(router_module, "get_boz_tbills", only91)
    assert client.get("/api/v1/treasury-bills/364D").status_code == 404
    body = client.get("/api/v1/treasury-bills/latest").json()
    assert body["missing_tenors"] == ["182D", "273D", "364D"]


@pytest.mark.parametrize("exc,status", [
    (httpx.ReadTimeout("secret-internal-url"), 504),
    (httpx.ConnectError("secret-internal-url"), 502),
    (ValueError("secret-internal-url"), 502),
])
def test_upstream_error_mapping_is_safe(monkeypatch, exc, status):
    async def boom():
        raise exc
    monkeypatch.setattr(router_module, "get_boz_tbills", boom)
    r = TestClient(app).get("/api/v1/treasury-bills/latest")
    assert r.status_code == status and "secret" not in r.text and "jsonapi" not in r.text


def test_persistence_failure_still_returns_live_data(client, monkeypatch):
    def fail(rows):
        raise tbill_store.TbillConflictError("x")
    monkeypatch.setattr(router_module, "save_tbill_auctions", fail)
    assert client.get("/api/v1/treasury-bills/latest").status_code == 200


def test_adapter_uses_mock_transport_and_rejects_non_json():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=payload(), headers={"content-type": "application/vnd.api+json"})
    async def run(h):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await boz_treasury.get_boz_tbills(client=c)
    import asyncio
    rows, skipped = asyncio.run(run(handler))
    assert "page%5Blimit%5D=5" in seen["url"] and len(rows) == 6 and [r.tender_number for r in skipped] == ["99/2026"]
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
    sql = (Path(__file__).parents[3] / "db/init/003_treasury_bill_auctions.sql").read_text()
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS treasury_bill_auctions"))
        c.execute(text(sql))
    yield eng
    eng.dispose()


def test_pg_idempotent_exact_and_no_raw(engine):
    rows, _ = parsed()
    assert tbill_store.save_tbill_auctions(rows, engine=engine) == (len(rows), 0)
    assert tbill_store.save_tbill_auctions(rows, engine=engine) == (0, len(rows))
    with engine.connect() as c:
        stored = c.execute(text("SELECT * FROM treasury_bill_auctions ORDER BY id")).mappings().all()
    assert len(stored) == len(rows)
    assert str(stored[0]["yield_rate"]) == "7.4000" or any(str(r["yield_rate"]) == "9.4000" for r in stored)
    assert stored[0]["source"] == "boz" and stored[0]["retrieved_at"] == NOW
    assert not set(stored[0]) & {"payload", "raw", "raw_payload", "response"}


def test_pg_numeric_equal_is_noop_and_conflict_rolls_back(engine):
    tbill_store.save_tbill_auctions([row()], engine=engine)
    # 9.40 == 9.4000 numerically: no-op
    assert tbill_store.save_tbill_auctions([row(yield_rate="9.40")], engine=engine) == (0, 1)
    new = row(tenor="91D", tenor_days=91, isin="ZM9000000011", tender_number="97/2026")
    with pytest.raises(tbill_store.TbillConflictError):
        tbill_store.save_tbill_auctions([new, row(yield_rate="9.5000")], engine=engine)
    with engine.connect() as c:
        n, y = c.execute(text("SELECT count(*), max(yield_rate) FROM treasury_bill_auctions")).one()
    assert n == 1 and y == Decimal("9.4000")  # new row rolled back, original untouched
