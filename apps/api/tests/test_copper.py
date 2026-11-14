import asyncio
import copy
import importlib
import inspect
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
from app.schemas.copper import COPPER_SOURCE_DEFINITION, COPPER_STALE_AFTER_MONTHS, CopperPoint
from app.services import imf_pcps
from app.services.copper_store import (
    CopperConflictError, fetch_copper_history, fetch_latest_copper, is_stale, month_end, save_copper_months,
)
from app.services.imf_pcps import (
    IMF_COPPER_URL, MAX_RESPONSE_BYTES, CopperMonth, ImfCopperError, fetch_copper_payload, new_client, parse_copper_series,
)
from app.services.provider_policy import IMF_COPPER_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
UTC = timezone.utc


def run(coro):
    return asyncio.run(coro)


def make_payload(series=None, *, indicator="PCOPP", transformation="USD", frequency="M", country="G001", derivation=None, dataflow="IMF.RES:PCPS(9.0.0)"):
    """The real IMF SDMX 3.0 shape: dimension values are lists of ids, observations are indexed into the TIME_PERIOD values."""
    series = series if series is not None else {"1992-M01": "2150.57962901611", "1992-M02": "2208.89225860596", "1992-M03": "2231.79"}
    periods = list(series)
    rows = {str(i): [series[p], None, 0 if derivation is None else derivation] for i, p in enumerate(periods)}
    return {"meta": {}, "data": {
        "dataSets": [{"structure": 0, "action": "Replace", "series": {"0:0:0:0": {"attributes": [0, None, None], "observations": rows}}}],
        "structures": [{
            "dataSets": [0],
            "links": [{"urn": f"urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow={dataflow}", "title": "Dataflow"}],
            "dimensions": {
                "series": [
                    {"id": "COUNTRY", "keyPosition": 0, "values": [{"id": country}]},
                    {"id": "INDICATOR", "keyPosition": 1, "values": [{"id": indicator}]},
                    {"id": "DATA_TRANSFORMATION", "keyPosition": 2, "values": [{"id": transformation}]},
                    {"id": "FREQUENCY", "keyPosition": 3, "values": [{"id": frequency}]}],
                "observation": [{"id": "TIME_PERIOD", "keyPosition": 4, "values": [{"value": p} for p in periods]}]},
            "attributes": {"observation": [{"id": "PRECISION", "values": []}, {"id": "DERIVATION_TYPE", "values": [{"id": "R"}, {"id": "SE"}]}]},
        }]}}


# ---------------- parser ----------------
def test_valid_series_exact_decimals_ascending_and_typed():
    months = parse_copper_series(make_payload({"1992-M03": "2231.79", "1992-M01": "2150.57962901611", "1992-M02": "2208.89225860596"}))
    assert [m.period_start for m in months] == [date(1992, 1, 1), date(1992, 2, 1), date(1992, 3, 1)]  # normalised ascending
    assert months[0].value == Decimal("2150.57962901611") and str(months[0].value) == "2150.57962901611"  # not quantized, not via float
    assert all(isinstance(m.value, Decimal) for m in months)
    assert str(parse_copper_series(make_payload({"2026-M10": "14507.97363636364"}))[0].value) == "14507.97363636364"


def test_the_real_shape_of_the_series_is_resolved_by_ids_not_positions():
    payload = make_payload()
    payload["data"]["structures"][0]["dimensions"]["series"].reverse()  # dimension order changed: still the same four ids
    assert len(parse_copper_series(payload)) == 3


@pytest.mark.parametrize("kwargs", [
    {"indicator": "PGOLD"}, {"indicator": "PEXGALL"}, {"transformation": "INDEX"}, {"transformation": "INDEX_PCH"}, {"transformation": "INDEX_PCHY"},
    {"frequency": "A"}, {"frequency": "Q"}, {"country": "USA"}, {"country": "G163"}, {"dataflow": "IMF.RES:OTHER(1.0.0)"}])
def test_wrong_series_identity_is_rejected(kwargs):
    with pytest.raises(ImfCopperError):
        parse_copper_series(make_payload(**kwargs))


def test_annual_and_quarterly_period_labels_are_rejected_even_if_dimensions_claim_monthly():
    for periods in ({"1992": "100.5"}, {"1992-Q1": "100.5"}, {"2026-Q3": "100.5"}):
        with pytest.raises(ImfCopperError):
            parse_copper_series(make_payload(periods))


@pytest.mark.parametrize("period", ["1992-M13", "1992-M00", "1992-M1", "1992-01", "92-M01", "1992-M01 ", "", None, 199201])
def test_malformed_periods_are_rejected(period):
    payload = make_payload({"1992-M01": "100"})
    payload["data"]["structures"][0]["dimensions"]["observation"][0]["values"][0]["value"] = period
    with pytest.raises(ImfCopperError):
        parse_copper_series(payload)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-5", "0", "0.00", "1e5", "1,000", "", " 12", "abc", "12.", ".5", None, 12.5, 12, True])
def test_malformed_nonfinite_or_non_positive_values_are_rejected(value):
    payload = make_payload({"1992-M01": "100"})
    payload["data"]["dataSets"][0]["series"]["0:0:0:0"]["observations"]["0"][0] = value
    with pytest.raises(ImfCopperError):
        parse_copper_series(payload)


def test_duplicate_months_are_rejected_not_resolved():
    payload = make_payload({"1992-M01": "100", "1992-M02": "101"})
    payload["data"]["structures"][0]["dimensions"]["observation"][0]["values"][1]["value"] = "1992-M01"  # two indices, one month
    with pytest.raises(ImfCopperError):
        parse_copper_series(payload)


def test_one_malformed_observation_fails_the_whole_series_without_a_partial_result():
    payload = make_payload({"1992-M01": "100", "1992-M02": "101", "1992-M03": "102"})
    payload["data"]["dataSets"][0]["series"]["0:0:0:0"]["observations"]["1"][0] = "oops"
    with pytest.raises(ImfCopperError):
        parse_copper_series(payload)


def test_future_valid_months_and_arbitrary_length_are_accepted_and_gaps_are_not_filled():
    months = parse_copper_series(make_payload({"2026-M10": "14507.97", "2027-M01": "15000.5", "2027-M02": "15100"}))  # no Nov/Dec 2026
    assert [m.period_start for m in months] == [date(2026, 10, 1), date(2027, 1, 1), date(2027, 2, 1)]
    assert len(months) == 3  # nothing was invented for the missing months


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("data"), lambda p: p["data"].pop("structures"), lambda p: p["data"].update(dataSets=[]),
    lambda p: p["data"]["dataSets"][0].update(series={}), lambda p: p["data"]["dataSets"][0]["series"].update({"0:0:0:1": {"observations": {}}}),
    lambda p: p["data"]["dataSets"][0]["series"]["0:0:0:0"].update(observations={}),
    lambda p: p["data"]["structures"][0]["dimensions"]["series"][0]["values"].append({"id": "USA"}),
    lambda p: p["data"]["structures"][0]["dimensions"]["series"].pop(),
    lambda p: p["data"]["structures"][0]["dimensions"]["series"].append({"id": "EXTRA", "values": [{"id": "x"}]}),
    lambda p: p["data"]["structures"][0]["dimensions"].update(observation=[]),
    lambda p: p["data"]["structures"][0].update(links=[]),
    lambda p: p["data"]["dataSets"][0]["series"]["0:0:0:0"]["observations"].update({"99": ["100", None, 0]}),
    lambda p: p["data"]["dataSets"][0]["series"]["0:0:0:0"]["observations"].update({"-1": ["100", None, 0]}),
])
def test_unexpected_structures_are_rejected(mutate):
    payload = copy.deepcopy(make_payload())
    mutate(payload)
    with pytest.raises((ImfCopperError, KeyError)) as info:
        parse_copper_series(payload)
    assert isinstance(info.value, ImfCopperError)  # always the deliberate, safe error type


@pytest.mark.parametrize("payload", [None, [], "x", 5, {"data": None}, {"data": []}])
def test_non_object_payloads_are_rejected(payload):
    with pytest.raises(ImfCopperError):
        parse_copper_series(payload)


def test_observations_flagged_as_estimates_or_derived_values_are_refused():
    with pytest.raises(ImfCopperError):
        parse_copper_series(make_payload(derivation=1))  # DERIVATION_TYPE code index 1 = "SE" (staff estimate)
    assert len(parse_copper_series(make_payload(derivation=0))) == 3  # "R" raw data is accepted


# ---------------- network adapter ----------------
def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_exact_series_url_is_https_api_imf_org_only():
    assert IMF_COPPER_URL == "https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/PCPS/+/G001.PCOPP.USD.M"
    assert imf_pcps.IMF_HOST == "api.imf.org" and "*" not in IMF_COPPER_URL and "key" not in IMF_COPPER_URL.lower()
    source = inspect.getsource(imf_pcps)
    assert "api_key" not in source and "Authorization" not in source and "open(" not in source and "write_bytes" not in source


def test_one_request_with_json_accept_and_the_parsed_series():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=make_payload(), headers={"content-type": "application/json; charset=utf-8"})
    payload = run(fetch_copper_payload(client_for(handler)))
    (request,) = seen
    assert str(request.url) == IMF_COPPER_URL and request.method == "GET" and request.headers["accept"] == "application/json"
    assert len(parse_copper_series(payload)) == 3


def test_new_client_has_a_timeout_user_agent_and_no_automatic_redirects():
    client = new_client()
    assert client.timeout.read == imf_pcps.IMF_TIMEOUT_SECONDS and "ZM-Terminal" in client.headers["user-agent"] and client.follow_redirects is False
    assert "authorization" not in client.headers


def test_failure_modes_are_safe_and_not_retried():
    calls = []

    def status(code):
        def handler(request):
            calls.append(code)
            return httpx.Response(code)
        return handler
    for code in (404, 429, 500, 503):
        calls.clear()
        with pytest.raises(httpx.HTTPStatusError):
            run(fetch_copper_payload(client_for(status(code))))
        assert calls == [code]  # exactly one attempt
    with pytest.raises(ImfCopperError):
        run(fetch_copper_payload(client_for(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))))
    with pytest.raises(ImfCopperError):
        run(fetch_copper_payload(client_for(lambda r: httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}))))
    with pytest.raises(ImfCopperError):  # an absent Content-Type is not accepted either
        run(fetch_copper_payload(client_for(lambda r: httpx.Response(200, content=b"{}"))))


def test_redirects_are_never_followed_even_to_the_same_host():
    for location in ("https://evil.example/x", "https://api.imf.org/elsewhere", "/relative"):
        with pytest.raises(ImfCopperError):
            run(fetch_copper_payload(client_for(lambda r, l=location: httpx.Response(302, headers={"location": l}))))


def test_timeout_and_oversized_responses_are_refused():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(httpx.TimeoutException):
        run(fetch_copper_payload(client_for(slow)))
    big = b'{"x":"' + b"a" * (MAX_RESPONSE_BYTES + 10) + b'"}'
    with pytest.raises(ImfCopperError):
        run(fetch_copper_payload(client_for(lambda r: httpx.Response(200, content=big, headers={"content-type": "application/json"}))))


# ---------------- provider policy ----------------
def test_imf_policy_is_local_only_and_does_not_decide_the_lme_question():
    p = IMF_COPPER_POLICY
    assert (p.provider_id, p.dataset_id, p.name, p.source_url) == (
        "imf", "imf_pcps_copper", "International Monetary Fund, Primary Commodity Prices", "https://data.imf.org/en/datasets/IMF.RES:PCPS")
    assert (p.classification, p.personal_data_expected, p.authentication) == ("public_market_data", False, "none")
    assert (p.normalized_storage_allowed, p.raw_response_storage, p.attribution_required) == (True, "disabled", True)
    assert (p.redistribution, p.commercial_use) == ("review_required", "review_required")
    assert set(p.required_provenance) >= {"source", "source_series", "source_url", "unit", "currency", "source_definition"}
    assert set(p.required_timestamps) == {"period_start", "period_end", "retrieved_at"}
    assert PROVIDER_POLICIES[p.dataset_id] is p
    guidance = p.request_guidance.lower()
    assert "local/internal" in guidance and "manual import" in guidance and "g001.pcopp.usd.m" in guidance and "conflict" in guidance
    assert "no polling" in guidance and "no raw response" in guidance
    assert "copyright@imf.org" in guidance and "unresolved" in guidance
    for claim in ("lme licence is required", "lme licence is not required", "lme rights are cleared", "separate lme licence is required", "no lme licence"):
        assert claim not in guidance


# ---------------- schemas / staleness ----------------
def test_point_schema_calendar_month_and_plain_decimal_serialization():
    point = CopperPoint(period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), value=Decimal("14507.97363636364"))
    assert point.model_dump(mode="json")["value"] == "14507.97363636364"
    assert CopperPoint(period_start=date(2024, 2, 1), period_end=date(2024, 2, 29), value=Decimal("1E+4")).model_dump(mode="json")["value"] == "10000"
    for start, end in ((date(2026, 2, 2), date(2026, 2, 28)), (date(2026, 2, 1), date(2026, 2, 27)), (date(2026, 2, 1), date(2026, 3, 31))):
        with pytest.raises(ValueError):
            CopperPoint(period_start=start, period_end=end, value=Decimal(1))
    assert month_end(date(2024, 2, 1)) == date(2024, 2, 29) and month_end(date(2026, 12, 1)) == date(2026, 12, 31)


def test_staleness_is_calendar_months_behind_the_current_lusaka_month():
    assert COPPER_STALE_AFTER_MONTHS == 2
    assert is_stale(date(2026, 10, 1), today=date(2026, 11, 14)) is False  # the previous month is normal
    assert is_stale(date(2026, 9, 1), today=date(2026, 11, 30)) is False   # two months behind is still not stale
    assert is_stale(date(2026, 8, 1), today=date(2026, 11, 1)) is True     # three calendar months behind
    assert is_stale(date(2025, 12, 1), today=date(2026, 2, 28)) is False and is_stale(date(2025, 11, 1), today=date(2026, 2, 1)) is True


# ---------------- API (offline) ----------------
client = TestClient(app)


def sample_observation(stale=False):
    from app.schemas.copper import CopperObservation
    return CopperObservation(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), value=Decimal("14507.97363636364"),
                             source_definition=COPPER_SOURCE_DEFINITION, source_url=IMF_COPPER_URL,
                             retrieved_at=datetime(2026, 11, 14, 12, tzinfo=UTC), stale=stale)


def test_latest_returns_the_stored_month_with_provenance_and_the_heuristic(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 11, 14))
    monkeypatch.setattr(router_module, "fetch_latest_copper", lambda today: seen.append(today) or sample_observation())
    body = client.get("/api/v1/copper/latest").json()
    assert seen == [date(2026, 11, 14)]  # Africa/Lusaka today, not browser or server-local time
    o = body["observation"]
    assert o["value"] == "14507.97363636364" and (o["benchmark"], o["source"], o["source_series"], o["currency"], o["unit"], o["price_basis"]) == (
        "COPPER", "imf", "PCOPP", "USD", "metric_ton", "monthly_average")
    assert o["source_definition"] == COPPER_SOURCE_DEFINITION and o["source_url"] == IMF_COPPER_URL
    assert (o["period_start"], o["period_end"], o["stale"], o["stale_after_months"]) == ("2026-10-01", "2026-10-31", False, 2)
    assert o["retrieved_at"].startswith("2026-11-14T12:00:00") and "publication_date" not in o


def test_empty_database_is_a_deliberate_structured_empty_state(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_latest_copper", lambda today: None)
    r = client.get("/api/v1/copper/latest")
    assert r.status_code == 200 and r.json() == {"observation": None}


def test_history_is_stored_only_validates_the_range_and_defaults_to_5y(monkeypatch):
    seen = []
    points = [CopperPoint(period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), value=Decimal("14473.05590909091")),
              CopperPoint(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), value=Decimal("14507.97363636364"))]
    monkeypatch.setattr(router_module, "fetch_copper_history", lambda r, today: seen.append(r) or points)
    body = client.get("/api/v1/copper/history?range=3Y").json()
    assert seen == ["3Y"] and body["count"] == 2 and body["range"] == "3Y" and body["benchmark"] == "COPPER"
    assert [p["period_start"] for p in body["observations"]] == ["2026-09-01", "2026-10-01"]  # ascending, nothing filled
    assert client.get("/api/v1/copper/history").status_code == 200 and seen[-1] == "5Y"
    for bad in ("1M", "3M", "2W", "all", ""):
        assert client.get("/api/v1/copper/history", params={"range": bad}).status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/copper/latest", "/api/v1/copper/history?range=ALL"])
def test_database_failures_are_a_safe_503(monkeypatch, path):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 14507.97")
    monkeypatch.setattr(router_module, "fetch_latest_copper", boom)
    monkeypatch.setattr(router_module, "fetch_copper_history", boom)
    r = client.get(path)
    assert r.status_code == 503 and "SECRET" not in r.text and "14507" not in r.text


def test_the_api_never_reaches_the_imf(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("copper API routes must read PostgreSQL only")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(router_module, "fetch_latest_copper", lambda today: sample_observation())
    monkeypatch.setattr(router_module, "fetch_copper_history", lambda r, today: [])
    assert client.get("/api/v1/copper/latest").status_code == 200 and client.get("/api/v1/copper/history?range=1Y").status_code == 200
    assert not hasattr(router_module, "fetch_copper_payload") and "imf_pcps" not in inspect.getsource(router_module)
    from app.services import copper_store
    assert "httpx" not in inspect.getsource(copper_store) and "imf_pcps import CopperMonth" in inspect.getsource(copper_store)


def test_openapi_has_both_routes_and_no_collision():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/copper/latest" in paths and "/api/v1/copper/history" in paths


# ---------------- importer (offline) ----------------
def serve(series, count=None):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=make_payload(series), headers={"content-type": "application/json"})
    return handler, seen


SERIES = {"2026-M01": "12000.5", "2026-M02": "12100.25", "2026-M04": "12890.68772727273", "2026-M05": "13512.15619047619"}  # March is not published
NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def test_dry_run_selects_counts_and_writes_nothing(monkeypatch):
    from app.jobs import backfill_copper
    handler, seen = serve(SERIES)
    monkeypatch.setattr(backfill_copper, "save_copper_months", lambda *a, **k: pytest.fail("dry run must not write"))
    result = run(backfill_copper.run_import(date(2026, 1, 1), None, dry_run=True, client=client_for(handler), now=NOW))
    assert result == {"months_requested": 5, "selected": 4, "not_published": 1, "inserted": 0, "already_present": 0}
    assert len(seen) == 1  # one source request for a normal import


def test_explicit_range_defaults_and_rejections():
    from app.jobs import backfill_copper
    handler, seen = serve(SERIES)
    r = run(backfill_copper.run_import(date(2026, 2, 1), date(2026, 4, 1), dry_run=True, client=client_for(handler), now=NOW))
    assert (r["months_requested"], r["selected"], r["not_published"]) == (3, 2, 1)
    for start, end in ((date(2025, 12, 1), None), (date(2026, 1, 1), date(2026, 6, 1)), (date(2026, 6, 1), None)):
        with pytest.raises(backfill_copper.BackfillError):
            run(backfill_copper.run_import(start, end, dry_run=True, client=client_for(serve(SERIES)[0]), now=NOW))
    seen.clear()
    with pytest.raises(backfill_copper.BackfillError):
        run(backfill_copper.run_import(date(2026, 4, 1), date(2026, 2, 1), dry_run=True, client=client_for(handler), now=NOW))
    assert seen == []  # an explicit reversed range is rejected before any request


def test_cli_parsing_and_safe_errors(monkeypatch, capsys):
    from app.jobs import backfill_copper
    for bad in ("2026-1", "202601", "2026-13", "2026-00", "x", "2026-01-01"):
        with pytest.raises(Exception):
            backfill_copper.parse_month(bad)
    assert backfill_copper.parse_month("1992-01") == date(1992, 1, 1)

    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(backfill_copper, "run_import", boom)
    assert backfill_copper.main(["--from", "1992-01", "--dry-run"]) == 1
    assert "SECRET" not in capsys.readouterr().err

    async def conflict(*a, **k):
        raise CopperConflictError(1, ["COPPER 2026-04"])
    monkeypatch.setattr(backfill_copper, "run_import", conflict)
    assert backfill_copper.main(["--from", "1992-01"]) == 1
    err = capsys.readouterr().err
    assert "COPPER 2026-04" in err and "nothing was written" in err and "12890" not in err

    async def ok(*a, **k):
        return {"months_requested": 3, "selected": 3, "not_published": 0, "inserted": 3, "already_present": 0}
    monkeypatch.setattr(backfill_copper, "run_import", ok)
    assert backfill_copper.main(["--from", "2026-01"]) == 0
    assert capsys.readouterr().out.strip() == "months_requested=3 selected=3 not_published=0 inserted=3 already_present=0"
    with pytest.raises(SystemExit):
        backfill_copper.main([])  # --from is required


def test_conflict_error_lists_months_not_prices():
    e = CopperConflictError(7, [f"COPPER 2026-{m:02d}" for m in range(1, 8)])
    assert "7 stored copper month(s)" in str(e) and "COPPER 2026-01" in str(e) and "COPPER 2026-06" not in str(e) and "and 2 more" in str(e)


# ---------------- PostgreSQL (opt-in) ----------------
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
        c.execute(text("DROP TABLE IF EXISTS global_copper_prices"))
        c.execute(text((init / "011_global_copper_prices.sql").read_text()))
    yield eng
    eng.dispose()


def months(*pairs):
    return [CopperMonth(date(y, m, 1), Decimal(v)) for (y, m), v in pairs]


def test_pg_exact_decimal_persistence_provenance_and_idempotency(engine):
    batch = months(((2026, 8), "14331.26476190476"), ((2026, 9), "14473.05590909091"), ((2026, 10), "14507.97363636364"))
    assert save_copper_months(batch, source_url=IMF_COPPER_URL, retrieved_at=NOW, engine=engine) == (3, 0)
    assert save_copper_months(batch, source_url=IMF_COPPER_URL, retrieved_at=NOW, engine=engine) == (0, 3)  # idempotent
    assert save_copper_months(months(((2026, 10), "14507.973636363640")), source_url="u", retrieved_at=NOW, engine=engine) == (0, 1)  # numerically equal
    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM global_copper_prices WHERE period_start = '2026-10-01'")).mappings().one()
        cols = [r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'global_copper_prices'"))]
    assert row["value"] == Decimal("14507.97363636364") and str(row["value"]) == "14507.97363636364"  # no quantization
    assert (row["provider"], row["benchmark"], row["currency"], row["unit"], row["price_basis"], row["source_series"]) == ("imf", "COPPER", "USD", "metric_ton", "monthly_average", "PCOPP")
    assert row["source_definition"] == COPPER_SOURCE_DEFINITION and row["source_url"] == IMF_COPPER_URL and row["period_end"] == date(2026, 10, 31)
    assert row["retrieved_at"] == NOW
    assert not [x for x in cols if "raw" in x or "json" in x or "payload" in x or "publication" in x]  # no raw storage, no invented publication date


def test_pg_conflict_rolls_back_the_whole_import_and_names_only_the_month(engine):
    save_copper_months(months(((2026, 1), "100.5"), ((2026, 2), "101.5")), source_url="u", retrieved_at=NOW, engine=engine)
    with pytest.raises(CopperConflictError) as info:
        save_copper_months(months(((2026, 2), "999.9"), ((2026, 3), "103"), ((2025, 12), "99")), source_url="u", retrieved_at=NOW, engine=engine)
    assert "COPPER 2026-02" in str(info.value) and "999" not in str(info.value) and info.value.count == 1
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM global_copper_prices")).scalar() == 2  # 2025-12 and 2026-03 were rolled back
        assert c.execute(text("SELECT value FROM global_copper_prices WHERE period_start = '2026-02-01'")).scalar() == Decimal("101.5")  # not overwritten


def test_pg_migration_constraints(engine):
    base = {"s": date(2026, 2, 1), "e": date(2026, 2, 28)}

    def insert(**over):
        params = {"provider": "imf", "benchmark": "COPPER", "s": base["s"], "e": base["e"], "currency": "USD", "unit": "metric_ton", "basis": "monthly_average", **over}
        with engine.begin() as c:
            c.execute(text("INSERT INTO global_copper_prices (provider, benchmark, period_start, period_end, value, currency, unit, price_basis, source_series, source_definition, source_url, retrieved_at) "
                           "VALUES (:provider, :benchmark, :s, :e, 1, :currency, :unit, :basis, 'PCOPP', 'd', 'u', now())"), params)
    insert()  # valid
    for bad in ({"provider": "world_bank"}, {"benchmark": "BRENT"}, {"currency": "ZMW"}, {"unit": "barrel"}, {"basis": "daily"},
                {"s": date(2026, 3, 2), "e": date(2026, 3, 31)}, {"s": date(2026, 3, 1), "e": date(2026, 3, 30)}, {"s": date(2026, 4, 1), "e": date(2026, 4, 30), "benchmark": "COPPER", "provider": "x"}):
        with pytest.raises(Exception):
            insert(**bad)
    with pytest.raises(Exception):
        insert()  # the identity (provider, benchmark, period) is unique


def test_pg_latest_history_ranges_ordering_and_missing_months(engine):
    assert fetch_latest_copper(today=date(2026, 11, 14), engine=engine) is None and fetch_copper_history("ALL", today=date(2026, 11, 14), engine=engine) == []
    batch = months(((2022, 5), "9000.1"), ((2024, 11), "9500"), ((2025, 11), "10000"), ((2026, 3), "12000"), ((2026, 10), "14507.97363636364"), ((2023, 1), "8800"))
    save_copper_months(batch, source_url=IMF_COPPER_URL, retrieved_at=NOW, engine=engine)  # unordered input; months in between are absent
    today = date(2026, 11, 14)
    latest = fetch_latest_copper(today=today, engine=engine)
    assert latest.period_start == date(2026, 10, 1) and latest.value == Decimal("14507.97363636364") and latest.stale is False
    assert latest.source_url == IMF_COPPER_URL and latest.retrieved_at == NOW and latest.stale_after_months == 2
    allh = fetch_copper_history("ALL", today=today, engine=engine)
    assert [p.period_start for p in allh] == sorted(p.period_start for p in allh) and len(allh) == 6  # ascending, nothing filled
    assert [p.period_start for p in fetch_copper_history("1Y", today=today, engine=engine)] == [date(2025, 11, 1), date(2026, 3, 1), date(2026, 10, 1)]
    assert [p.period_start for p in fetch_copper_history("3Y", today=today, engine=engine)] == [date(2024, 11, 1), date(2025, 11, 1), date(2026, 3, 1), date(2026, 10, 1)]
    assert len(fetch_copper_history("5Y", today=today, engine=engine)) == 6
    assert fetch_latest_copper(today=date(2027, 3, 1), engine=engine).stale is True  # five months behind
    assert fetch_latest_copper(today=date(2026, 12, 31), engine=engine).stale is False  # two months behind
    assert fetch_latest_copper(today=date(2027, 1, 1), engine=engine).stale is True  # three months behind


def test_pg_end_to_end_import_with_a_mocked_source(engine):
    from app.jobs import backfill_copper
    handler, seen = serve(SERIES)
    result = run(backfill_copper.run_import(date(2026, 1, 1), None, client=client_for(handler), engine=engine, now=NOW))
    assert result == {"months_requested": 5, "selected": 4, "not_published": 1, "inserted": 4, "already_present": 0} and len(seen) == 1
    again = run(backfill_copper.run_import(date(2026, 1, 1), None, client=client_for(serve(SERIES)[0]), engine=engine, now=NOW))
    assert (again["inserted"], again["already_present"]) == (0, 4)
    revised = dict(SERIES, **{"2026-M02": "12100.30"})
    with pytest.raises(CopperConflictError) as info:
        run(backfill_copper.run_import(date(2026, 1, 1), None, client=client_for(serve(revised)[0]), engine=engine, now=NOW))
    assert "COPPER 2026-02" in str(info.value)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM global_copper_prices")).scalar() == 4
        assert c.execute(text("SELECT value FROM global_copper_prices WHERE period_start = '2026-02-01'")).scalar() == Decimal("12100.25")
        assert c.execute(text("SELECT count(*) FROM global_copper_prices WHERE period_start = '2026-03-01'")).scalar() == 0  # March was not invented


def test_pg_api_reads_use_the_lusaka_calendar_and_stay_stored_only(engine, monkeypatch):
    save_copper_months(months(((2026, 8), "14331.26"), ((2026, 10), "14507.97")), source_url="u", retrieved_at=NOW, engine=engine)
    monkeypatch.setattr(router_module, "fetch_latest_copper", lambda today: fetch_latest_copper(today=today, engine=engine))
    monkeypatch.setattr(router_module, "fetch_copper_history", lambda r, today: fetch_copper_history(r, today=today, engine=engine))
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 12, 31))
    body = client.get("/api/v1/copper/latest").json()["observation"]
    assert body["period_start"] == "2026-10-01" and body["stale"] is False  # two calendar months behind December
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2027, 1, 1))
    assert client.get("/api/v1/copper/latest").json()["observation"]["stale"] is True  # three calendar months behind January
    history = client.get("/api/v1/copper/history?range=ALL").json()
    assert [p["period_start"] for p in history["observations"]] == ["2026-08-01", "2026-10-01"] and history["count"] == 2
