"""Stored-only Bank FX read service and API on a guarded disposable PostgreSQL database. SYNTHETIC data only; no network."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import bank_fx_builders as b
from app.main import app
from app.schemas import bank_fx as schemas
from app.services import bank_fx_read as read
from app.services import boz_bank_fx
from app.services.bank_fx_store import save_bank_fx_snapshots
from app.services.boz_bank_fx import parse_bank_fx_document
from bank_fx_db import bank_fx_engine
from datetime import date, datetime, timezone

NOW = datetime(2030, 1, 3, 8, 0, tzinfo=timezone.utc)
U1, U2, U3 = ("00000000-0000-4000-8000-0000000000c%d" % i for i in (1, 2, 3))
LATEST, HISTORY = "/api/v1/fx/banks/latest", "/api/v1/fx/banks/history"


@pytest.fixture
def engine(disposable_database, monkeypatch):
    with bank_fx_engine(disposable_database) as (admin, _role):
        monkeypatch.setattr(read, "get_engine", lambda: admin)
        yield admin


def store(engine, *nodes):
    save_bank_fx_snapshots(parse_bank_fx_document(b.document(*nodes), retrieved_at=NOW, source_url="https://example.test/p"), engine=engine)


def at(day):
    return f"2030-01-{day:02d}T15:36:15+02:00"


def banks(values=None, published=3, layout="v2"):
    return b.default_banks(layout, published)


def names(slot):
    return [x.institution_name for x in slot.banks]


# ------------------------------------------------------------------------------------------------------------------ service
def test_empty_database(engine):
    assert read.fetch_latest_bank_fx(engine=engine).snapshot is None
    assert read.fetch_bank_fx_date(date(2030, 1, 2), engine=engine) is None


def test_one_date_latest_and_exact(engine):
    store(engine, b.node(uuid=U1, nid=1, vid=1))
    snap = read.fetch_latest_bank_fx(engine=engine).snapshot
    assert snap.observation_date == date(2030, 1, 2) and snap.table_layout == "v2" and snap.rights_status == "review_required"
    assert [s.slot for s in snap.slots] == ["09:30", "12:30", "15:30"]
    assert snap.source_record_id == U1 and snap.source_revision_id == 1 and snap.source_rates_datetime_raw == "2030-01-02T15:36:15+02:00"
    assert read.fetch_bank_fx_date(date(2030, 1, 2), engine=engine) == snap
    assert read.fetch_bank_fx_date(date(2030, 1, 1), engine=engine) is None and read.fetch_bank_fx_date(date(2030, 1, 3), engine=engine) is None


def test_newest_observation_date_wins_even_if_an_older_date_was_created_later(engine):
    store(engine, b.node(uuid=U1, nid=1, vid=1, rates_date=at(2), created="2030-02-01T00:00:00+00:00"),
          b.node(uuid=U2, nid=2, vid=2, rates_date=at(3), created="2030-01-03T10:00:00+00:00"))
    assert read.fetch_latest_bank_fx(engine=engine).snapshot.observation_date == date(2030, 1, 3)


def test_canonical_node_then_revision_selection_follows_the_view(engine):
    store(engine,
          b.node(uuid=U1, nid=1, vid=1, created="2030-01-02T10:00:00+00:00"),
          b.node(uuid=U2, nid=2, vid=2, created="2030-01-02T11:00:00+00:00", revision="2030-01-02T11:00:00+00:00"),
          b.node(uuid=U2, nid=2, vid=3, created="2030-01-02T11:00:00+00:00", revision="2030-01-02T12:00:00+00:00"))
    snap = read.fetch_bank_fx_date(date(2030, 1, 2), engine=engine)
    assert (snap.source_record_id, snap.source_revision_id) == (U2, 3)  # newest node, then its newest revision


def test_progressive_snapshots_are_never_merged(engine):
    old = b.node(uuid=U1, nid=1, vid=1, created="2030-01-02T10:00:00+00:00", banks=banks(published=3))
    new = b.node(uuid=U2, nid=2, vid=2, created="2030-01-02T11:00:00+00:00", banks=banks(published=2))
    store(engine, old, new)
    snap = read.fetch_bank_fx_date(date(2030, 1, 2), engine=engine)
    assert snap.source_record_id == U2 and [s.slot for s in snap.slots] == ["09:30", "12:30"]  # no 15:30 borrowed from the older node


def test_source_precision_is_returned_exactly(engine):
    rows = [b.bank_row("A BANK", (["9.9", "9.90", "9.900", "9.9000"], None, None)),
            b.bank_row("B BANK", (["10.0001", "10.1", "10.12", "10.123"], None, None)),
            b.bank_row("Market Average", (["8.5", "8.55", "8.555", "8.5555"], None, None))]
    store(engine, b.node(layout="v1", banks=rows))
    (slot,) = read.fetch_latest_bank_fx(engine=engine).snapshot.slots
    first = slot.banks[0]
    assert (first.interbank_buy, first.interbank_sell, first.retail_buy, first.retail_sell) == ("9.9", "9.90", "9.900", "9.9000")
    assert slot.banks[1].interbank_buy == "10.0001"
    assert slot.market_average.retail_sell == "8.5555"


@pytest.mark.parametrize("layout,label", [("v1", "Retail Rates"), ("v2", "BOARD RATES")])
def test_source_label_is_dynamic(engine, layout, label):
    store(engine, b.node(layout=layout))
    assert {s.retail_source_label for s in read.fetch_latest_bank_fx(engine=engine).snapshot.slots} == {label}


def test_market_average_is_separate_and_banks_keep_source_row_order(engine):
    rows = [b.bank_row(n, (b.quartet(10 + i), None, None)) for i, n in enumerate(("ZED BANK", "ALPHA BANK", "MID BANK"))]
    rows.insert(1, b.bank_row("MARKET AVERAGE", (b.quartet(5), None, None)))
    store(engine, b.node(banks=rows))
    (slot,) = read.fetch_latest_bank_fx(engine=engine).snapshot.slots
    assert names(slot) == ["ZED BANK", "ALPHA BANK", "MID BANK"]  # not alphabetical; the average is not a bank
    assert slot.market_average.institution_name == "MARKET AVERAGE" and slot.market_average.source_row_index == 4
    assert [x.source_row_index for x in slot.banks] == [3, 5, 6]


def test_a_slot_without_a_market_average_row_has_null_average(engine):
    rows = [b.bank_row("A BANK", (b.quartet(10), b.quartet(11), None)), b.bank_row("MARKET AVERAGE", (b.quartet(5), None, None))]
    store(engine, b.node(banks=rows))
    first, second = read.fetch_latest_bank_fx(engine=engine).snapshot.slots
    assert first.market_average is not None and second.market_average is None and second.slot == "12:30"


def test_inconsistent_stored_child_rows_fail_closed():
    # direct check of the defensive validators on rows the database would normally prevent
    base = dict(slot="09:30", source_row_index=3, institution_name="A", institution_name_raw="A", interbank_buy_text="1.0",
                interbank_sell_text="1.0", retail_buy_text="1.0", retail_sell_text="1.0")
    with pytest.raises(read.BankFxReadError):
        read._slots([{**base, "is_market_average": False, "retail_source_label": "BOARD RATES"},
                     {**base, "source_row_index": 4, "is_market_average": False, "retail_source_label": "Retail Rates"}])
    with pytest.raises(read.BankFxReadError):
        read._slots([{**base, "is_market_average": True, "retail_source_label": "X"}, {**base, "source_row_index": 4, "is_market_average": True, "retail_source_label": "X"}])
    with pytest.raises(read.BankFxReadError):
        read._slots([{**base, "slot": "10:00", "is_market_average": False, "retail_source_label": "X"}])


def test_the_read_connection_is_read_only(engine):
    store(engine, b.node())
    with read._read_only(engine) as c:
        with pytest.raises(Exception, match="read-only"):
            c.execute(read.text("DELETE FROM public.bank_fx_rates"))


# ------------------------------------------------------------------------------------------------------------------ API
@pytest.fixture
def client(monkeypatch):
    async def refuse(*a, **k):
        raise AssertionError("a stored-only endpoint touched the network adapter")

    for name in ("fetch_bank_fx_payload", "fetch_bank_fx_snapshots"):
        monkeypatch.setattr(boz_bank_fx, name, refuse)
    monkeypatch.setattr(boz_bank_fx, "new_client", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    return TestClient(app)


def test_routes_are_registered_and_not_captured_by_other_fx_routes():
    from app.api.router import router

    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/api/v1/fx/banks/latest" in paths and "/api/v1/fx/banks/history" in paths
    for path in paths:
        if path.startswith("/api/v1/fx/") and path not in ("/api/v1/fx/banks/latest", "/api/v1/fx/banks/history"):
            assert "{" not in path  # no dynamic /fx/{...} route that could swallow /fx/banks/...


def test_api_latest_empty_is_200_with_null_snapshot(engine, client):
    response = client.get(LATEST)
    assert response.status_code == 200 and response.json() == {"snapshot": None}


def test_api_latest_and_history_populated_with_string_rates(engine, client):
    rows = [b.bank_row("A BANK  ", (["9.9", "9.90", "9.900", "9.9000"], None, None)), b.bank_row("MARKET AVERAGE", (b.quartet(5), None, None))]
    store(engine, b.node(layout="v1", banks=rows))
    for url in (LATEST, HISTORY + "?date=2030-01-02"):
        response = client.get(url)
        assert response.status_code == 200
        body = response.json()
        snap = body["snapshot"] if url == LATEST else body
        assert snap["observation_date"] == "2030-01-02" and snap["table_layout"] == "v1" and snap["rights_status"] == "review_required"
        (slot,) = snap["slots"]
        assert slot["retail_source_label"] == "Retail Rates" and len(slot["banks"]) == 1
        bank = slot["banks"][0]
        assert bank["institution_name"] == "A BANK" and bank["institution_name_raw"] == "A BANK  "
        assert [bank[k] for k in ("interbank_buy", "interbank_sell", "retail_buy", "retail_sell")] == ["9.9", "9.90", "9.900", "9.9000"]
        assert slot["market_average"]["institution_name"] == "MARKET AVERAGE"
        assert "9.9" in response.text and '9.9,' not in response.text and ": 9.9" not in response.text  # strings, never JSON numbers
    assert not any(isinstance(v, float) for v in _walk(json.loads(response.text)))


def _walk(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def test_api_history_missing_date_is_404_without_fallback(engine, client):
    store(engine, b.node())
    assert client.get(HISTORY + "?date=2030-01-01").status_code == 404
    assert client.get(HISTORY + "?date=2030-01-03").status_code == 404
    assert client.get(HISTORY + "?date=2030-01-02").status_code == 200


@pytest.mark.parametrize("query", ["", "?date=", "?date=2030-1-2", "?date=2030-02-30", "?date=latest", "?date=2030-01-02&date=2030-01-03x"])
def test_api_history_rejects_bad_dates(client, query):
    assert client.get(HISTORY + query).status_code == 422


def test_api_read_failure_is_503_and_generic(monkeypatch, client):
    def boom(*a, **k):
        raise RuntimeError("postgresql://user:secret@db/zm_terminal")

    monkeypatch.setattr("app.api.router.fetch_latest_bank_fx", boom)
    monkeypatch.setattr("app.api.router.fetch_bank_fx_date", boom)
    for url in (LATEST, HISTORY + "?date=2030-01-02"):
        response = client.get(url)
        assert response.status_code == 503 and "secret" not in response.text and "postgresql" not in response.text


def test_api_schema_forbids_float_like_rates():
    with pytest.raises(Exception):
        schemas.BankFxInstitutionRate(source_row_index=3, institution_name="A", institution_name_raw="A", interbank_buy=9.9,
                                      interbank_sell="1.0", retail_buy="1.0", retail_sell="1.0")


def test_read_modules_do_not_import_the_network_adapter_parser_or_importer():
    from pathlib import Path

    root = Path(read.__file__).parents[1]
    for path in (root / "services/bank_fx_read.py", root / "schemas/bank_fx.py"):
        source = path.read_text()
        for forbidden in ("boz_bank_fx", "import_bank_fx", "httpx", "bank_fx_store"):
            assert forbidden not in source
    router = (root / "api/router.py").read_text()
    assert "boz_bank_fx" not in router and "import_bank_fx" not in router
