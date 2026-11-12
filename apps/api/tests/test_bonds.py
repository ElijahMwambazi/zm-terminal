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


def placeholder(title, day):
    return {"type": "node--bond_auction", "id": "ph" + title, "attributes": {
        "title": title, "field_bond_auction_date": day, "status": True},
        "relationships": {"field_auction_results": {"data": []}}}


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
    lambda p: p["data"][2]["attributes"].update(field_bond_auction_date="2026-10-02T15:00:00"),  # no offset
    lambda p: p["data"][2]["attributes"].update(field_bond_auction_date="not-a-date"),
    lambda p: p["included"].remove(para(p, "b10")),  # referenced record missing: ambiguous tender
    lambda p: p["data"][2]["relationships"]["field_auction_results"]["data"].append({"type": "x", "id": "b10"}),  # duplicate reference
    lambda p: p["data"][2]["relationships"].pop("field_auction_results"),
])
def test_tender_level_failure_rejects_whole_tender(mutate):
    rows, rejected = parsed(mutate)
    mine = [r for r in rejected if r.scope == "tender" and r.auction_date != date(2026, 11, 27)]
    assert len(mine) == 1 and mine[0].affected_tenors is None
    assert "97/2026/BA" not in {r.tender_number for r in rows}
    assert {r.tender_number for r in rows} == {"98/2026/BA", "96/2026/BA"}


@pytest.mark.parametrize("mutate", [
    lambda p: para(p, "b10")["attributes"].update(field_10_year_isin="XX123"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_isin="ZM9100000048 "),  # trailing space: not trimmed
    lambda p: para(p, "b10")["attributes"].update(field_10_year_isin="ZM910000048"),  # short: not repaired
    lambda p: para(p, "b10")["attributes"].update(field_10_year_yield_rate="Nil"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_coupon_rate="1e1"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_coupon_rate="0"),
    lambda p: para(p, "b10")["attributes"].pop("field_10_year_yield_rate"),
    lambda p: para(p, "b10")["attributes"].update(field_10_year_tenor="7_years"),
    lambda p: para(p, "b10")["attributes"].update(status=False),
])
def test_tenor_level_failure_rejects_only_that_tenor(mutate):
    rows, rejected = parsed(mutate)
    mine = [r for r in rejected if r.tender_number == "97/2026/BA"]
    assert [(r.scope, r.affected_tenors) for r in mine] == [("tenor", ("10Y",))]
    got = {r.tenor for r in rows if r.tender_number == "97/2026/BA"}
    assert got == {"2Y", "3Y", "5Y", "7Y", "15Y"}  # valid siblings stay usable


def test_bad_15y_isin_keeps_siblings_and_bad_5y_isin_drops_only_5y():
    rows, rejected = parsed(lambda p: para(p, "b15")["attributes"].update(field_15_year_isin="ZM100007709"))
    assert {r.tenor for r in rows if r.tender_number == "97/2026/BA"} == {"2Y", "3Y", "5Y", "7Y", "10Y"}
    rows, rejected = parsed(lambda p: para(p, "b5")["attributes"].update(field_5_year_isin="ZM100007626"))
    assert {r.tenor for r in rows if r.tender_number == "97/2026/BA"} == {"2Y", "3Y", "7Y", "10Y", "15Y"}
    assert ("97/2026/BA", ("5Y",)) in [(r.tender_number, r.affected_tenors) for r in rejected]


def test_unsupported_tenor_record_is_ignored_and_unknown_type_is_a_record_rejection():
    def mutate(p):
        extra = {"type": "paragraph--20_year_bond", "id": "x20", "attributes": {"status": True}}
        junk = {"type": "paragraph--mystery", "id": "xm", "attributes": {"status": True}}
        p["included"] += [extra, junk]
        p["data"][2]["relationships"]["field_auction_results"]["data"] += [{"type": "x", "id": "x20"}, {"type": "x", "id": "xm"}]
    rows, rejected = parsed(mutate)
    assert {r.tenor for r in rows if r.tender_number == "97/2026/BA"} == {"2Y", "3Y", "5Y", "7Y", "10Y", "15Y"}
    mine = [r for r in rejected if r.tender_number == "97/2026/BA"]
    assert [(r.scope, r.affected_tenors) for r in mine] == [("tenor", None)]  # unknown tenor: may affect any


def test_no_results_tender_is_not_malformed_and_never_creates_fallback():
    def mutate(p):
        p["data"].pop(0)  # drop the real rejected tender
        p["data"].insert(0, placeholder("99/2026/BA", "2026-11-27T10:00:00+02:00"))  # newest, pending entry
    rows, rejected = parsed(mutate)
    assert rejected == []  # not reported as malformed source data
    assert all(r.newer_rejected_tenders == [] for r in latest_per_tenor(rows, rejected))


def test_rejected_metadata_is_safe():
    _, rejected = parsed(lambda p: para(p, "b10")["attributes"].update(field_10_year_isin="SECRET-BAD-VALUE"))
    dumped = json.dumps([r.model_dump(mode="json") for r in rejected])
    assert "SECRET" not in dumped and set(rejected[0].model_dump()) == {"tender_number", "auction_date", "affected_tenors", "scope"}


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
    assert "page%5Blimit%5D=50" in seen["url"] and "bond_auction" in seen["url"]
    assert len(rows) == 13 and [r.tender_number for r in rejected] == ["99/2026/BA"]
    with pytest.raises(ValueError):
        asyncio.run(run(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})))


# ---- pagination across the BoZ archive ----
NEXT = "https://www.boz.zm/jsonapi/node/bond_auction?page%5Boffset%5D={}&page%5Blimit%5D=10"
by_title = {n["attributes"]["title"]: n for n in FIXTURE["data"]}


def page(titles, *, next_offset=None, mutate=None, drop=()):
    p = {"jsonapi": {"version": "1.0"}, "data": [copy.deepcopy(by_title[t]) for t in titles],
         "included": copy.deepcopy(FIXTURE["included"]), "links": {}}
    if next_offset is not None:
        p["links"]["next"] = {"href": NEXT.format(next_offset)}
    for pid in drop:  # a tenor the tender legitimately did not auction
        for n in p["data"]:
            n["relationships"]["field_auction_results"]["data"] = [
                r for r in n["relationships"]["field_auction_results"]["data"] if r["id"] != pid]
    if mutate:
        mutate(p)
    return p


class Archive:
    """Offline BoZ: serves pages by `page[offset]`; records every request."""

    def __init__(self, pages):
        self.pages, self.requests = pages, []

    def handler(self, request):
        self.requests.append(str(request.url))
        idx = int(request.url.params.get("page[offset]", 0) or 0)
        # offset N means "page N" here (each test's links.next uses the page number)
        page_ = self.pages[idx]
        if isinstance(page_, Exception):
            raise page_
        return httpx.Response(200, json=page_, headers={"content-type": "application/vnd.api+json"})

    async def fetch(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as c:
            return await boz_bonds.get_boz_bonds(client=c)


def test_five_year_found_on_second_page():
    a = Archive([page(["99/2026/BA", "98/2026/BA"], next_offset=1), page(["97/2026/BA", "96/2026/BA"])])
    rows, _ = asyncio.run(a.fetch())
    got = {r.tenor: r for r in latest_per_tenor(rows)}
    assert got["5Y"].tender_number == "97/2026/BA" and got["5Y"].auction_date == date(2026, 10, 2)
    assert len(a.requests) == 2 and "page%5Blimit%5D=50" in a.requests[0] and "sort=-field_bond_auction_date" in a.requests[0]


def test_traversal_stops_once_all_six_tenors_found():
    a = Archive([page(["98/2026/BA"], next_offset=1), page(["97/2026/BA"], next_offset=2), page(["96/2026/BA"])])
    asyncio.run(a.fetch())
    assert len(a.requests) == 2  # page 3 never requested


def test_traversal_stops_at_archive_end_and_reports_missing():
    a = Archive([page(["98/2026/BA"], next_offset=1), page(["96/2026/BA"])])  # no 5Y anywhere
    rows, _ = asyncio.run(a.fetch())
    assert len(a.requests) == 2 and "5Y" not in {r.tenor for r in rows}


def test_missing_tenors_only_after_completed_search(monkeypatch):
    done = Archive([page(["97/2026/BA", "96/2026/BA"], drop=["b5"])])
    async def complete():
        return await done.fetch()
    monkeypatch.setattr(router_module, "get_boz_bonds", complete)
    assert TestClient(app).get("/api/v1/government-bonds/latest").json()["missing_tenors"] == ["5Y"]
    # a truncated search must fail closed instead of claiming 5Y is missing
    monkeypatch.setattr(boz_bonds, "MAX_BOND_PAGES", 2)
    cut = Archive([page(["96/2026/BA"], next_offset=1, drop=["a2"]), page(["96/2026/BA"], next_offset=2, drop=["a2"])])
    async def truncated():
        return await cut.fetch()
    monkeypatch.setattr(router_module, "get_boz_bonds", truncated)
    r = TestClient(app).get("/api/v1/government-bonds/latest")
    assert r.status_code == 502 and "missing" not in r.text and len(cut.requests) == 2


def test_page_cap_with_next_link_fails_closed(monkeypatch):
    monkeypatch.setattr(boz_bonds, "MAX_BOND_PAGES", 2)
    partial = lambda: page(["98/2026/BA"], next_offset=1)  # never has 3Y/5Y/7Y/15Y
    a = Archive([partial(), page(["98/2026/BA"], next_offset=2), partial()])
    with pytest.raises(ValueError, match="truncated"):
        asyncio.run(a.fetch())
    assert len(a.requests) == 2


def test_rejected_tender_on_earlier_page_flags_older_rows_from_later_page():
    a = Archive([page(["99/2026/BA", "98/2026/BA"], next_offset=1), page(["97/2026/BA"])])
    rows, rejected = asyncio.run(a.fetch())
    got = {r.tenor: r for r in latest_per_tenor(rows, rejected)}
    assert got["5Y"].newer_rejected_tenders == ["99/2026/BA"]  # selected from page 2
    assert got["10Y"].newer_rejected_tenders == ["99/2026/BA"]


def test_older_rejected_tender_on_later_page_does_not_flag_newer_rows():
    def break_96(p):
        para(p, "a10")["attributes"].pop("field_10_year_isin")
    a = Archive([page(["98/2026/BA"], next_offset=1), page(["97/2026/BA", "96/2026/BA"], mutate=break_96)])
    rows, rejected = asyncio.run(a.fetch())
    assert [r.tender_number for r in rejected] == ["96/2026/BA"]
    assert all(r.newer_rejected_tenders == [] for r in latest_per_tenor(rows, rejected))


def test_rejected_page_does_not_stop_traversal():
    def break_all(p):
        for n in p["data"]:
            n["attributes"]["title"] = "bad"
    a = Archive([page(["99/2026/BA"], next_offset=1, mutate=break_all), page(["97/2026/BA"])])
    rows, rejected = asyncio.run(a.fetch())
    assert len(rows) == 6 and len(rejected) == 1 and len(a.requests) == 2


def test_no_results_tenders_across_archive_never_flag_or_get_reported():
    def add(p):
        p["data"] += [placeholder("12/2025/BA", "2025-12-01T16:00:00+02:00"), placeholder("11/2024/BA", "2024-11-01T16:00:00+02:00")]
        p["data"].insert(0, placeholder("98/2026/BA", "2026-11-27T10:00:00+02:00"))
    a = Archive([page(["97/2026/BA"], mutate=add)])
    rows, rejected = asyncio.run(a.fetch())
    assert rejected == [] and all(r.newer_rejected_tenders == [] for r in latest_per_tenor(rows, rejected))


def test_tenor_with_only_a_rejected_record_is_missing_and_unverified(monkeypatch):
    def bad_5y(p):
        para(p, "b5")["attributes"]["field_5_year_isin"] = "ZM100007709"
    a2 = Archive([page(["98/2026/BA", "97/2026/BA"], mutate=bad_5y)])
    async def real2():
        return await a2.fetch()
    monkeypatch.setattr(router_module, "get_boz_bonds", real2)
    body = TestClient(app).get("/api/v1/government-bonds/latest").json()
    assert body["missing_tenors"] == ["5Y"] and body["unverified_tenors"] == ["5Y"]
    assert body["skipped_tenders"] == ["97/2026/BA"]
    assert [t["tenor"] for t in body["tenors"]] == ["2Y", "3Y", "7Y", "10Y", "15Y"]  # siblings kept
    assert TestClient(app).get("/api/v1/government-bonds/5Y").status_code == 404


def add_bad_record(p, title, years, pid):
    """A tenor record with a malformed ISIN inside the given (already present) tender."""
    names = {5: "field_5_year", 15: "field_15_year"}[years]
    p["included"].append({"type": f"paragraph--{years}_year{'s' if years == 5 else ''}_bond", "id": pid, "attributes": {
        "status": True, f"{names}_isin": "ZM1", f"{names}_coupon_rate": "10.0000",
        f"{names}_tenor": f"{years}_years", f"{names}_yield_rate": "10.0000"}})
    node = [n for n in p["data"] if n["attributes"]["title"] == title][0]
    node["relationships"]["field_auction_results"]["data"].append({"type": "x", "id": pid})


def latest_from(titles, mutate):
    p = page(titles, mutate=mutate)
    rows, rejected = parse_bond_auctions(p, retrieved_at=NOW)
    return {r.tenor: r for r in latest_per_tenor(rows, rejected)}


def test_newer_rejected_15y_does_not_flag_older_5y():
    got = latest_from(["98/2026/BA", "97/2026/BA"], lambda p: add_bad_record(p, "98/2026/BA", 15, "z15"))
    assert got["5Y"].newer_rejected_tenders == [] and got["10Y"].newer_rejected_tenders == []
    assert got["15Y"].newer_rejected_tenders == ["98/2026/BA"]  # the rejected tenor itself is uncertain


def test_newer_rejected_5y_flags_older_5y_only():
    got = latest_from(["98/2026/BA", "97/2026/BA"], lambda p: add_bad_record(p, "98/2026/BA", 5, "z5"))
    assert got["5Y"].newer_rejected_tenders == ["98/2026/BA"]
    assert got["15Y"].newer_rejected_tenders == [] and got["3Y"].newer_rejected_tenders == []


def test_tender_level_rejection_may_flag_all_older_tenors_it_could_affect():
    got = latest_from(["99/2026/BA", "98/2026/BA", "97/2026/BA"], None)  # 99/2026/BA has a duplicate 10Y: tender-level
    assert all(r.newer_rejected_tenders == ["99/2026/BA"] for r in got.values())


def test_persistence_receives_only_accepted_rows(monkeypatch):
    saved = []
    monkeypatch.setattr(router_module, "save_bond_auctions", lambda rows: saved.append(list(rows)) or (0, 0))
    a = Archive([page(["98/2026/BA", "97/2026/BA"], mutate=lambda p: (
        para(p, "b15")["attributes"].update(field_15_year_isin="ZM100007709"),
        add_bad_record(p, "98/2026/BA", 5, "z5")))])
    async def real():
        return await a.fetch()
    monkeypatch.setattr(router_module, "get_boz_bonds", real)
    assert TestClient(app).get("/api/v1/government-bonds/latest").status_code == 200
    keys = {(r.tender_number, r.tenor) for r in saved[0]}
    assert ("97/2026/BA", "15Y") not in keys and ("98/2026/BA", "5Y") not in keys
    assert ("97/2026/BA", "5Y") in keys and ("98/2026/BA", "10Y") in keys


def test_identical_duplicate_across_pages_collapses_and_conflict_fails():
    a = Archive([page(["98/2026/BA"], next_offset=1), page(["98/2026/BA", "97/2026/BA"])])
    rows, _ = asyncio.run(a.fetch())
    assert len(rows) == 8 and len({(r.tender_number, r.tenor) for r in rows}) == 8

    def change(p):
        para(p, "c10")["attributes"]["field_10_year_yield_rate"] = "13.9999"
    b = Archive([page(["98/2026/BA"], next_offset=1), page(["98/2026/BA", "97/2026/BA"], mutate=change)])
    with pytest.raises(ValueError, match="Conflicting"):
        asyncio.run(b.fetch())


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("t"), httpx.ConnectError("c"),
])
def test_later_page_transport_failure_propagates_for_safe_mapping(failure):
    a = Archive([page(["98/2026/BA"], next_offset=1), failure])
    with pytest.raises(type(failure)):
        asyncio.run(a.fetch())


def test_later_page_http_error_and_foreign_next_link_fail():
    def handler(request):
        if "page[offset]" in str(request.url) or "offset" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, json=page(["98/2026/BA"], next_offset=1),
                              headers={"content-type": "application/vnd.api+json"})
    async def run(h):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await boz_bonds.get_boz_bonds(client=c)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run(handler))
    def foreign(p):
        p["links"]["next"] = {"href": "https://evil.example/jsonapi/node/bond_auction?page%5Boffset%5D=1"}
    a = Archive([page(["98/2026/BA"], mutate=foreign)])
    with pytest.raises(ValueError, match="leaves"):
        asyncio.run(a.fetch())
    assert len(a.requests) == 1


def test_single_tenor_endpoint_returns_older_valid_auction_not_404(monkeypatch):
    a = Archive([page(["99/2026/BA", "98/2026/BA"], next_offset=1), page(["97/2026/BA"])])
    async def real():
        return await a.fetch()
    monkeypatch.setattr(router_module, "get_boz_bonds", real)
    r = TestClient(app).get("/api/v1/government-bonds/5Y")
    assert r.status_code == 200
    body = r.json()
    assert body["tender_number"] == "97/2026/BA" and body["auction_date"] == "2026-10-02" and body["yield_rate"] == "14.5000"
    assert "stale" not in body


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
