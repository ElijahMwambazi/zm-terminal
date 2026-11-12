import asyncio
import copy
import importlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_bonds, backfill_tbills
from app.main import app
from app.schemas.bonds import GovernmentBondHistoryPoint
from app.schemas.tbills import TreasuryBillHistoryPoint
from app.services import bond_store, tbill_store
from app.services.jsonapi import TruncatedSearchError

router_module = importlib.import_module("app.api.router")
FIX = Path(__file__).parent / "fixtures"
TB = json.loads((FIX / "boz_tbills.json").read_text())
BD = json.loads((FIX / "boz_bonds.json").read_text())
NOW = datetime(2026, 12, 1, 10, tzinfo=timezone.utc)
TB_URL = "https://www.boz.zm/jsonapi/node/tbills_auction_results"
BD_URL = "https://www.boz.zm/jsonapi/node/bond_auction"


def subset(doc, titles, next_href=None, extra_nodes=()):
    d = copy.deepcopy(doc)
    d["data"] = [n for n in d["data"] if n["attributes"]["title"] in titles] + [copy.deepcopy(n) for n in extra_nodes]
    d["links"] = {"next": {"href": next_href}} if next_href else {}
    return d


class Server:
    """Offline BoZ: serves a list of pages in order; records every request URL."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def handler(self, request):
        self.urls.append(str(request.url))
        page = self.pages[len(self.urls) - 1]
        return httpx.Response(200, json=page, headers={"content-type": "application/vnd.api+json"})

    async def run(self, job, *args, **kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as c:
            return await job.run_backfill(*args, client=c, now=NOW, pause=0, **kw)


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("database touched")
    monkeypatch.setattr(tbill_store, "get_engine", boom)
    monkeypatch.setattr(bond_store, "get_engine", boom)


# ---------------- Treasury Bill job ----------------
def test_tbill_job_range_filters_pagination_and_counts():
    p1 = subset(TB, {"99/2026", "98/2026"}, next_href=TB_URL + "?page%5Boffset%5D=2")
    p2 = subset(TB, {"97/2026"})
    s = Server([p1, p2])
    r = asyncio.run(s.run(backfill_tbills, date(2026, 10, 1), date(2026, 11, 30), dry_run=True))
    q = parse_qs(urlsplit(s.urls[0]).query)
    assert q["filter[lo][condition][value]"] == ["2026-10-01"] and q["filter[hi][condition][value]"] == ["2026-11-30"]
    assert q["filter[lo][condition][path]"] == ["field_tbills_auction_date"] and q["page[limit]"] == ["50"]
    assert len(s.urls) == 2 and "offset" in s.urls[1] and "filter" not in s.urls[1]  # follows links.next as given
    assert r == {"pages": 2, "tenders_fetched": 3, "no_results": 0, "valid_rows": 6, "rejected_tenders": 1,
                 "out_of_range": 0, "inserted": 0, "already_present": 0}  # 99/2026 malformed; 98 partial (2) + 97 (4)


def test_tbill_job_drops_out_of_range_and_counts_no_results_separately():
    empty = {"type": "node--tbills_auction_results", "id": "e1", "attributes": {"title": "01/2020", "field_tbills_auction_date": "2020-01-02", "status": True},
             "relationships": {"field_tbills_auction_results": {"data": []}}}
    s = Server([subset(TB, {"97/2026", "98/2026"}, extra_nodes=[empty])])
    r = asyncio.run(s.run(backfill_tbills, date(2026, 11, 1), date(2026, 11, 30), dry_run=True))
    assert r["valid_rows"] == 2 and r["out_of_range"] == 4 and r["no_results"] == 1 and r["rejected_tenders"] == 0


def test_tbill_job_rejects_bad_ranges_and_truncation():
    s = Server([])
    with pytest.raises(ValueError, match="future"):
        asyncio.run(s.run(backfill_tbills, date(2026, 11, 1), date(2027, 1, 1), dry_run=True))
    with pytest.raises(ValueError, match="after"):
        asyncio.run(s.run(backfill_tbills, date(2026, 11, 2), date(2026, 11, 1), dry_run=True))
    assert s.urls == []
    cut = Server([subset(TB, {"97/2026"}, next_href=TB_URL + "?page%5Boffset%5D=50")])
    with pytest.raises(TruncatedSearchError):
        asyncio.run(cut.run(backfill_tbills, date(2026, 1, 1), date(2026, 11, 30), dry_run=True, max_pages=1))
    foreign = Server([subset(TB, {"97/2026"}, next_href="https://evil.example/jsonapi/node/tbills_auction_results?x=1")])
    with pytest.raises(ValueError, match="leaves"):
        asyncio.run(foreign.run(backfill_tbills, date(2026, 1, 1), date(2026, 11, 30), dry_run=True))


def test_tbill_job_empty_range_is_fine_and_conflicting_pages_fail():
    empty = {"jsonapi": {}, "data": [], "links": {}}
    assert asyncio.run(Server([empty]).run(backfill_tbills, date(2026, 1, 1), date(2026, 1, 2), dry_run=True))["valid_rows"] == 0
    changed = subset(TB, {"97/2026"}, next_href=TB_URL + "?page%5Boffset%5D=1")
    again = subset(TB, {"97/2026"})
    [i for i in again["included"] if i["id"] == "p4"][0]["attributes"]["field_364_yield_rate"] = "9.9999"
    with pytest.raises(ValueError, match="Conflicting"):
        asyncio.run(Server([changed, again]).run(backfill_tbills, date(2026, 1, 1), date(2026, 12, 1), dry_run=True))


# ---------------- Government Bond job ----------------
def test_bond_job_filters_counts_siblings_and_tender_level():
    def bad_15y_and_dup(d):
        [i for i in d["included"] if i["id"] == "b15"][0]["attributes"]["field_15_year_isin"] = "ZM100007709"
    page = subset(BD, {"99/2026/BA", "98/2026/BA", "97/2026/BA", "96/2026/BA"})
    bad_15y_and_dup(page)
    s = Server([page])
    r = asyncio.run(s.run(backfill_bonds, date(2026, 9, 1), date(2026, 11, 30), dry_run=True))
    q = parse_qs(urlsplit(s.urls[0]).query)
    assert q["filter[lo][condition][value]"] == ["2026-09-01T00:00:00+02:00"] and q["filter[hi][condition][value]"] == ["2026-11-30T23:59:59+02:00"]
    assert r["tender_level_rejections"] == 1 and r["tenor_level_rejections"] == 1  # 99/2026/BA whole; 97's 15Y only
    assert r["valid_rows"] == 12  # 98: 2 + 97: 5 (15Y rejected) + 96: 5
    assert r["auctions_fetched"] == 4 and r["inserted"] == 0


def test_bond_job_paginates_and_stops_at_cap():
    p1 = subset(BD, {"98/2026/BA"}, next_href=BD_URL + "?page%5Boffset%5D=1")
    p2 = subset(BD, {"97/2026/BA", "96/2026/BA"})
    s = Server([p1, p2])
    r = asyncio.run(s.run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), dry_run=True))
    assert r["pages"] == 2 and r["valid_rows"] == 2 + 6 + 5
    cut = Server([subset(BD, {"98/2026/BA"}, next_href=BD_URL + "?page%5Boffset%5D=1")])
    with pytest.raises(TruncatedSearchError):
        asyncio.run(cut.run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), dry_run=True, max_pages=1))


def test_bond_job_counts_no_results_and_future_dates():
    ph = {"type": "node--bond_auction", "id": "ph", "attributes": {"title": "01/2020/BA", "field_bond_auction_date": "2026-05-01T10:00:00+02:00", "status": True},
          "relationships": {"field_auction_results": {"data": []}}}
    r = asyncio.run(Server([subset(BD, {"98/2026/BA"}, extra_nodes=[ph])]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), dry_run=True))
    assert r["no_results"] == 1 and r["tender_level_rejections"] == 0 and r["tenor_level_rejections"] == 0
    with pytest.raises(ValueError, match="future"):
        asyncio.run(Server([]).run(backfill_bonds, date(2026, 1, 1), date(2027, 1, 1), dry_run=True))


def test_cli_dry_run_prints_counts_only(monkeypatch, capsys):
    async def fake(start, end, dry_run=False, **kw):
        return {"valid_rows": 3, "inserted": 0}
    monkeypatch.setattr(backfill_bonds, "run_backfill", fake)
    assert backfill_bonds.main(["--from", "2026-01-01", "--to", "2026-02-01", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run valid_rows=3 inserted=0"
    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 13.8")
    monkeypatch.setattr(backfill_bonds, "run_backfill", boom)
    assert backfill_bonds.main(["--from", "2026-01-01"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err and "13.8" not in err


# ---------------- history API (offline) ----------------
def tpoint(day, y="9.4000"):
    return TreasuryBillHistoryPoint(tenor="364D", auction_date=day, tender_number="1/2026", isin="ZM9000000045", discount_rate="8.5900", yield_rate=y)


def test_tbill_history_api_validation_serialization_and_503(monkeypatch):
    c = TestClient(app)
    seen = {}
    def fake(days, rng, today):
        seen.update(days=days, rng=rng)
        return [tpoint(date(2026, 1, 8), "9.4000"), tpoint(date(2026, 3, 5), "9.5")]
    monkeypatch.setattr(router_module, "fetch_tbill_history", fake)
    body = c.get("/api/v1/treasury-bills/history").json()  # defaults
    assert seen == {"days": 364, "rng": "1Y"} and body["tenor"] == "364D" and body["count"] == 2
    assert body["observations"][0]["yield_rate"] == "9.4000" and body["observations"][1]["yield_rate"] == "9.5"
    assert {"tenor", "auction_date", "tender_number", "isin", "discount_rate", "yield_rate", "source"} <= set(body["observations"][0])
    for bad in ("tenor=30D", "tenor=364d", "range=2Y", "range=1m"):
        assert c.get(f"/api/v1/treasury-bills/history?{bad}").status_code == 422
    def down(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_tbill_history", down)
    r = c.get("/api/v1/treasury-bills/history?tenor=91D&range=ALL")
    assert r.status_code == 503 and "SECRET" not in r.text


def test_history_routes_do_not_shadow_tenor_routes_and_never_call_boz(monkeypatch):
    async def boom():
        raise AssertionError("BoZ called from a history endpoint")
    monkeypatch.setattr(router_module, "get_boz_tbills", boom)
    monkeypatch.setattr(router_module, "get_boz_bonds", boom)
    monkeypatch.setattr(router_module, "fetch_tbill_history", lambda *a, **k: [])
    monkeypatch.setattr(router_module, "fetch_bond_history", lambda *a, **k: [])
    c = TestClient(app)
    assert c.get("/api/v1/treasury-bills/history").json()["observations"] == []
    assert c.get("/api/v1/government-bonds/history").json()["observations"] == []


def test_bond_history_api_validation_and_503(monkeypatch):
    c = TestClient(app)
    pts = [GovernmentBondHistoryPoint(tenor="10Y", auction_date=date(2026, d, 1), tender_number=f"{d}/2026/BA", isin="ZM9100000048",
                                      coupon_rate="16.6000", yield_rate=y) for d, y in ((1, "14.5"), (3, "14.2500"), (6, "13.7500"))]
    monkeypatch.setattr(router_module, "fetch_bond_history", lambda days, rng, today: pts)
    body = c.get("/api/v1/government-bonds/history?tenor=10Y&range=ALL").json()
    assert [o["isin"] for o in body["observations"]] == ["ZM9100000048"] * 3 and body["count"] == 3  # not deduplicated by ISIN
    assert body["observations"][0]["yield_rate"] == "14.5"
    for bad in ("tenor=4Y", "range=1M", "range=3m", "tenor=10y"):
        assert c.get(f"/api/v1/government-bonds/history?{bad}").status_code == 422
    monkeypatch.setattr(router_module, "fetch_bond_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.get("/api/v1/government-bonds/history").status_code == 503


# ---------------- real PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    init = Path(__file__).parents[3] / "db/init"
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS treasury_bill_auctions"))
        c.execute(text("DROP TABLE IF EXISTS government_bond_auctions"))
        for f in ("003_treasury_bill_auctions.sql", "004_government_bond_auctions.sql", "005_fixed_income_history_indexes.sql"):
            c.execute(text((init / f).read_text()))
    yield eng
    eng.dispose()


def count(engine, table):
    with engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def test_pg_tbill_import_idempotent_conflict_rollback_and_history(engine):
    page = subset(TB, {"99/2026", "98/2026", "97/2026"})
    first = asyncio.run(Server([page]).run(backfill_tbills, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    assert first["inserted"] == 6 and first["already_present"] == 0 and first["rejected_tenders"] == 1
    again = asyncio.run(Server([page]).run(backfill_tbills, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    assert again["inserted"] == 0 and again["already_present"] == 6 and count(engine, "treasury_bill_auctions") == 6
    # a different value for an existing key: nothing from the batch may be written
    with engine.begin() as c:
        c.execute(text("DELETE FROM treasury_bill_auctions WHERE tender_number='97/2026' AND tenor_days=91"))
        c.execute(text("UPDATE treasury_bill_auctions SET yield_rate = 1.0 WHERE tender_number='98/2026' AND tenor_days=364"))
    with pytest.raises(tbill_store.TbillConflictError):
        asyncio.run(Server([page]).run(backfill_tbills, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    assert count(engine, "treasury_bill_auctions") == 5  # the deleted row was NOT re-inserted


def test_pg_tbill_history_filtering_order_no_interpolation(engine):
    rows = [(date(2026, 1, 8), "01/2026", 364, "9.40"), (date(2026, 6, 4), "10/2026", 364, "10.0000"),
            (date(2026, 3, 5), "05/2026", 364, "9.5000"), (date(2026, 3, 5), "05/2026", 91, "8.0000")]
    with engine.begin() as c:
        for d, t, days, y in rows:
            c.execute(text("INSERT INTO treasury_bill_auctions (auction_date,tender_number,tenor_days,isin,discount_rate,yield_rate,source,source_url,retrieved_at)"
                           " VALUES (:d,:t,:days,'ZM9000000045',8.5,:y,'boz','u',now())"), {"d": d, "t": t, "days": days, "y": Decimal(y)})
    today = date(2026, 7, 1)
    allp = tbill_store.fetch_tbill_history(364, "ALL", today=today, engine=engine)
    assert [p.auction_date for p in allp] == [date(2026, 1, 8), date(2026, 3, 5), date(2026, 6, 4)]  # ascending, 3 real points only
    assert [str(p.yield_rate) for p in allp] == ["9.40", "9.5000", "10.0000"] and all(p.tenor == "364D" for p in allp)
    assert [p.auction_date for p in tbill_store.fetch_tbill_history(364, "3M", today=today, engine=engine)] == [date(2026, 6, 4)]
    assert [p.auction_date for p in tbill_store.fetch_tbill_history(364, "6M", today=date(2026, 9, 1), engine=engine)] == [date(2026, 3, 5), date(2026, 6, 4)]
    assert len(tbill_store.fetch_tbill_history(364, "6M", today=today, engine=engine)) == 3
    assert len(tbill_store.fetch_tbill_history(91, "ALL", today=today, engine=engine)) == 1
    assert tbill_store.fetch_tbill_history(182, "ALL", today=today, engine=engine) == []


def test_pg_bond_import_siblings_reopened_isin_5y_and_history(engine):
    page = subset(BD, {"99/2026/BA", "98/2026/BA", "97/2026/BA", "96/2026/BA"})
    [i for i in page["included"] if i["id"] == "b15"][0]["attributes"]["field_15_year_isin"] = "ZM100007709"
    r = asyncio.run(Server([page]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    assert r["inserted"] == 12 and r["tender_level_rejections"] == 1 and r["tenor_level_rejections"] == 1
    assert asyncio.run(Server([page]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), engine=engine))["already_present"] == 12
    today = date(2026, 12, 1)
    ten = bond_store.fetch_bond_history(10, "ALL", today=today, engine=engine)
    assert [p.auction_date for p in ten] == [date(2026, 9, 4), date(2026, 10, 2), date(2026, 10, 30)]
    assert {p.isin for p in ten} == {"ZM9100000048"}  # same reopened ISIN, three observations
    assert [str(p.yield_rate) for p in ten] == ["14.0000", "13.8000", "13.5000"]
    five = bond_store.fetch_bond_history(5, "ALL", today=today, engine=engine)
    assert [(p.tender_number, str(p.yield_rate)) for p in five] == [("97/2026/BA", "14.5000")]  # only the real 5Y auction
    assert [p.tenor for p in bond_store.fetch_bond_history(15, "ALL", today=today, engine=engine)] == ["15Y"]  # 96's 15Y; 97's rejected
    assert [p.auction_date for p in bond_store.fetch_bond_history(10, "3M", today=date(2026, 12, 15), engine=engine)] == [date(2026, 10, 2), date(2026, 10, 30)]


def test_pg_bond_conflict_rolls_back_whole_batch(engine):
    page = subset(BD, {"98/2026/BA", "97/2026/BA"})
    asyncio.run(Server([page]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    with engine.begin() as c:
        c.execute(text("DELETE FROM government_bond_auctions WHERE tender_number='97/2026/BA' AND tenor_years=2"))
        c.execute(text("UPDATE government_bond_auctions SET yield_rate = 1.0 WHERE tender_number='98/2026/BA' AND tenor_years=10"))
    with pytest.raises(bond_store.BondConflictError):
        asyncio.run(Server([page]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    assert count(engine, "government_bond_auctions") == 7


def test_pg_history_api_reads_database_and_hides_failures(engine, monkeypatch):
    monkeypatch.setattr(router_module, "fetch_bond_history", lambda d, r, today: bond_store.fetch_bond_history(d, r, today=today, engine=engine))
    asyncio.run(Server([subset(BD, {"97/2026/BA"})]).run(backfill_bonds, date(2026, 1, 1), date(2026, 11, 30), engine=engine))
    body = TestClient(app).get("/api/v1/government-bonds/history?tenor=5Y&range=ALL").json()
    assert body["count"] == 1 and body["observations"][0]["coupon_rate"] == "14.5000"
