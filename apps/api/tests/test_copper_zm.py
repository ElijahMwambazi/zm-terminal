import importlib
import os
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.copper import COPPER_SOURCE_DEFINITION, COPPER_STALE_AFTER_MONTHS
from app.schemas.copper_zm import (
    COPPER_ZM_METHODOLOGY, COPPER_ZM_METHODOLOGY_ID, COPPER_ZM_NOTICE, COPPER_ZM_TIMING_NOTE, CopperZmMonth,
)
from app.services import copper_zm
from app.services.copper_store import save_copper_months
from app.services.copper_zm import CopperRow, derive_copper_zm
from app.services.imf_pcps import IMF_COPPER_URL, CopperMonth
from app.services.oil_zm import FxMid, derive_oil_zm, BrentMonth

router_module = importlib.import_module("app.api.router")
client = TestClient(app)
TODAY = date(2026, 11, 14)
RETRIEVED = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)


def utc(text_: str) -> datetime:
    return datetime.fromisoformat(text_.replace("Z", "+00:00"))


def fx(moment: str, mid: str) -> FxMid:
    return FxMid(observed_at=utc(moment), mid=Decimal(mid))


def cu(year: int, month: int, value: str) -> CopperRow:
    first = date(year, month, 1)
    last = date.fromordinal(date(year + (month == 12), month % 12 + 1, 1).toordinal() - 1)
    return CopperRow(first, last, Decimal(value), COPPER_SOURCE_DEFINITION, IMF_COPPER_URL, RETRIEVED)


def derive(copper, fx_obs, today=TODAY):
    return derive_copper_zm(copper, fx_obs, today=today)


def dec40(expression):
    with localcontext() as ctx:
        ctx.prec = 40
        return Decimal(format(expression().normalize(), "f"))


# ---------------- arithmetic ----------------
def test_one_day_one_observation():
    (m,), omitted = derive([cu(2026, 3, "10000")], [fx("2026-03-02T07:30:00Z", "20")])
    assert omitted == 0
    assert (m.zmw_per_mt, m.usd_zmw_observed_days_avg, m.copper_usd_per_mt) == (Decimal("200000"), Decimal("20"), Decimal("10000"))
    assert (m.fx_day_count, m.fx_observation_count, m.unit, m.currency, m.classification) == (1, 1, "metric_ton", "ZMW", "derived")
    assert (m.period_start, m.period_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert m.methodology_id == COPPER_ZM_METHODOLOGY_ID == "copper_zm_v1"


def test_multiple_observations_in_one_day_are_averaged_first():
    (m,), _ = derive([cu(2026, 3, "100")], [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-02T10:30:00Z", "22")])
    assert m.usd_zmw_observed_days_avg == Decimal("21") and (m.fx_day_count, m.fx_observation_count) == (1, 2)


def test_days_are_weighted_equally_not_observations():
    obs = [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-03T07:30:00Z", "30"), fx("2026-03-03T09:00:00Z", "30"), fx("2026-03-03T11:00:00Z", "30")]
    (m,), _ = derive([cu(2026, 3, "100")], obs)
    assert m.usd_zmw_observed_days_avg == Decimal("25")  # not (20+30*3)/4 = 27.5: a day with 3 observations is not 3x
    assert (m.fx_day_count, m.fx_observation_count) == (2, 4)


def test_repeating_decimals_use_40_digit_context_and_exact_inputs():
    obs = [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-03T07:30:00Z", "20"), fx("2026-03-04T07:30:00Z", "21")]
    (m,), _ = derive([cu(2026, 3, "14507.97363636364")], obs)
    fx_avg = dec40(lambda: Decimal(61) / Decimal(3))
    assert m.usd_zmw_observed_days_avg == fx_avg
    assert m.zmw_per_mt == dec40(lambda: Decimal("14507.97363636364") * (Decimal(61) / Decimal(3)))
    assert len(str(m.usd_zmw_observed_days_avg).replace(".", "")) <= 40 and isinstance(m.zmw_per_mt, Decimal)
    assert m.copper_usd_per_mt == Decimal("14507.97363636364")  # input untouched


@pytest.mark.parametrize("value,expected", [("0", "0"), ("-100", "-2000"), ("-0.5", "-10")])
def test_zero_and_negative_copper_are_multiplied_exactly(value, expected):
    (m,), _ = derive([cu(2026, 3, value)], [fx("2026-03-02T07:30:00Z", "20")])
    assert m.zmw_per_mt == Decimal(expected) and m.copper_usd_per_mt == Decimal(value)


def test_unsorted_input_gives_ascending_output():
    months, _ = derive([cu(2026, 4, "2"), cu(2026, 2, "1"), cu(2026, 3, "3")],
                       [fx("2026-04-02T07:30:00Z", "20"), fx("2026-02-02T07:30:00Z", "20"), fx("2026-03-02T07:30:00Z", "20")])
    assert [m.period_start.month for m in months] == [2, 3, 4]


# ---------------- availability ----------------
def test_copper_without_fx_is_omitted_and_counted_and_fx_without_copper_makes_no_point():
    months, omitted = derive([cu(2026, 2, "1"), cu(2026, 3, "2")], [fx("2026-03-02T07:30:00Z", "20"), fx("2026-04-02T07:30:00Z", "20")])
    assert [m.period_start for m in months] == [date(2026, 3, 1)] and omitted == 1  # April FX has no Copper: nothing


def test_no_fx_derives_nothing_and_pre_boz_history_is_not_filled():
    assert derive([cu(2025, 12, "1"), cu(2026, 1, "1")], []) == ([], 2)
    months, omitted = derive([cu(2025, 12, "1"), cu(2026, 1, "1")], [fx("2026-01-22T07:30:00Z", "19.6")])
    assert [m.period_start for m in months] == [date(2026, 1, 1)] and omitted == 1


def test_only_the_same_calendar_month_matches():
    months, omitted = derive([cu(2026, 3, "1")], [fx("2026-02-27T07:30:00Z", "20"), fx("2026-04-01T07:30:00Z", "21")])
    assert months == [] and omitted == 1  # neighbouring months are never substituted


def test_month_containing_the_start_of_stored_history_is_partial_and_sparse_later_months_are_not():
    obs = [fx("2026-01-22T07:30:00Z", "19.6"), fx("2026-01-23T07:30:00Z", "19.8"), fx("2026-02-16T07:30:00Z", "18.3"),
           fx("2026-03-02T07:30:00Z", "19.5")]
    months, _ = derive([cu(2026, 1, "1"), cu(2026, 2, "1"), cu(2026, 3, "1")], obs)
    assert [m.fx_coverage for m in months] == ["partial_source_window", "observed_days", "observed_days"]  # one-day Feb is just sparse
    assert months[0].usd_zmw_observed_days_avg == Decimal("19.7")


def test_history_starting_on_day_one_is_observed_days():
    (m,), _ = derive([cu(2026, 3, "1")], [fx("2026-03-01T07:30:00Z", "20")])
    assert m.fx_coverage == "observed_days"


def test_lusaka_date_boundary_near_utc_midnight():
    # 22:30Z on 31 Jan is 00:30 on 1 Feb in Africa/Lusaka (UTC+2): it belongs to February
    months, omitted = derive([cu(2026, 1, "1"), cu(2026, 2, "1")], [fx("2026-01-31T22:30:00Z", "20")])
    assert [m.period_start for m in months] == [date(2026, 2, 1)] and omitted == 1
    assert months[0].fx_first_date == date(2026, 2, 1)
    # 21:59Z on 28 Feb is still 28 Feb; 22:00Z is 1 March
    months, _ = derive([cu(2026, 2, "1"), cu(2026, 3, "1")], [fx("2026-02-28T21:59:00Z", "20"), fx("2026-02-28T22:00:00Z", "30")])
    assert [(m.period_start.month, m.usd_zmw_observed_days_avg) for m in months] == [(2, Decimal("20")), (3, Decimal("30"))]


def test_matches_oil_zm_fx_methodology_exactly_for_the_same_stored_population():
    obs = [fx("2026-03-02T07:30:00Z", "20.1234"), fx("2026-03-02T10:30:00Z", "20.2"), fx("2026-03-03T07:30:00Z", "21"),
           fx("2026-03-05T07:30:00Z", "19.9999"), fx("2026-02-27T22:30:00Z", "18")]
    (c,), _ = derive([cu(2026, 3, "9000")], obs)
    (o,), _ = derive_oil_zm([BrentMonth(date(2026, 3, 1), date(2026, 3, 31), Decimal("100"), date(2026, 11, 3), "u")], obs, today=TODAY)
    assert c.usd_zmw_observed_days_avg == o.usd_zmw_observed_days_avg
    assert (c.fx_day_count, c.fx_observation_count, c.fx_coverage) == (o.fx_day_count, o.fx_observation_count, o.fx_coverage)
    assert (c.fx_first_date, c.fx_last_date) == (o.fx_first_date, o.fx_last_date)


def test_staleness_uses_the_copper_threshold_per_derived_month():
    fresh, _ = derive([cu(2026, 9, "1")], [fx("2026-09-02T07:30:00Z", "20")], today=date(2026, 11, 14))
    old, _ = derive([cu(2026, 8, "1")], [fx("2026-08-02T07:30:00Z", "20")], today=date(2026, 11, 14))
    assert (fresh[0].stale, old[0].stale, fresh[0].stale_after_months) == (False, True, COPPER_STALE_AFTER_MONTHS)


def test_provenance_and_methodology_metadata():
    (m,), _ = derive([cu(2026, 3, "10000")], [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-04T09:00:00Z", "21")])
    assert (m.copper_source, m.copper_source_series, m.copper_source_definition, m.copper_source_url) == ("imf", "PCOPP", COPPER_SOURCE_DEFINITION, IMF_COPPER_URL)
    assert m.copper_source_page_url == "https://data.imf.org/en/datasets/IMF.RES:PCPS" and m.copper_retrieved_at == RETRIEVED
    assert (m.fx_source, m.fx_pair, m.fx_price) == ("boz", "USDZMW", "mid")
    assert (m.fx_first_date, m.fx_last_date) == (date(2026, 3, 2), date(2026, 3, 4))
    assert m.fx_first_observation == utc("2026-03-02T07:30:00Z") and m.fx_last_observation == utc("2026-03-04T09:00:00Z")
    assert m.methodology == COPPER_ZM_METHODOLOGY and m.notice == COPPER_ZM_NOTICE and m.timing_note == COPPER_ZM_TIMING_NOTE
    assert "not simultaneous" not in m.timing_note and "different observation schedules" in m.timing_note
    dumped = m.model_dump(mode="json")
    assert "publication_date" not in dumped and dumped["zmw_per_mt"] == "205000" and dumped["fx_first_observation"].endswith("Z")
    for forbidden in ("CC BY", "cleared", "permitted", "freely"):
        assert forbidden not in m.notice + m.methodology + m.timing_note


# ---------------- API ----------------
def month(**kw):
    base = dict(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), zmw_per_mt="296000.5", copper_usd_per_mt="14507.97",
                usd_zmw_observed_days_avg="20.4", fx_day_count=5, fx_observation_count=15, fx_coverage="observed_days",
                fx_first_date=date(2026, 10, 1), fx_last_date=date(2026, 10, 9), fx_first_observation=utc("2026-10-01T07:30:00Z"),
                fx_last_observation=utc("2026-10-09T13:30:00Z"), copper_source_definition=COPPER_SOURCE_DEFINITION,
                copper_source_url=IMF_COPPER_URL, copper_retrieved_at=RETRIEVED, stale=False)
    base.update(kw)
    return CopperZmMonth(**base)


def test_latest_is_the_newest_derivable_month_with_full_provenance(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: ([month(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31)), month()], 0))
    body = client.get("/api/v1/copper/zm/latest").json()
    assert body["period_start"] == "2026-10-01" and body["classification"] == "derived" and body["derived"] is True
    assert body["methodology_id"] == "copper_zm_v1" and body["copper_source_series"] == "PCOPP" and body["fx_pair"] == "USDZMW"
    assert body["unit"] == "metric_ton" and body["currency"] == "ZMW" and isinstance(body["zmw_per_mt"], str)
    assert "publication_date" not in body and body["stale_after_months"] == 2


def test_latest_asks_for_all_months(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: seen.append(r) or ([month()], 0))
    client.get("/api/v1/copper/zm/latest")
    assert seen == ["ALL"]


def test_latest_without_overlap_is_a_safe_404(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: ([], 3))
    response = client.get("/api/v1/copper/zm/latest")
    assert response.status_code == 404 and response.json() == {"detail": "No month has both stored IMF Copper and stored BoZ USD/ZMW data"}


def test_history_default_range_slim_points_ascending_and_omissions(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: seen.append(r) or ([month(), month(period_start=date(2026, 11, 1), period_end=date(2026, 11, 30))], 2))
    body = client.get("/api/v1/copper/zm/history").json()
    assert seen == ["1Y"] and body["range"] == "1Y" and body["count"] == 2 and body["copper_months_without_fx"] == 2
    assert body["methodology_id"] == "copper_zm_v1" and body["timing_note"] == COPPER_ZM_TIMING_NOTE
    assert set(body["observations"][0]) == {"classification", "derived", "period_start", "period_end", "currency", "unit", "zmw_per_mt",
                                            "copper_usd_per_mt", "usd_zmw_observed_days_avg", "fx_day_count", "fx_observation_count", "fx_coverage"}


@pytest.mark.parametrize("r", ["1Y", "3Y", "5Y", "ALL"])
def test_history_accepts_the_copper_ranges(monkeypatch, r):
    seen = []
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda rg, today: seen.append(rg) or ([], 0))
    assert client.get(f"/api/v1/copper/zm/history?range={r}").status_code == 200 and seen == [r]


@pytest.mark.parametrize("r", ["1M", "3M", "6M", "2Y", "1y", "", "MAX"])
def test_history_rejects_other_ranges(r):
    assert client.get(f"/api/v1/copper/zm/history?range={r}").status_code == 422


def test_empty_history_is_a_normal_200(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: ([], 4))
    response = client.get("/api/v1/copper/zm/history?range=ALL")
    assert response.status_code == 200 and response.json()["count"] == 0 and response.json()["observations"] == []
    assert response.json()["copper_months_without_fx"] == 4


@pytest.mark.parametrize("path", ["/api/v1/copper/zm/latest", "/api/v1/copper/zm/history"])
def test_database_failure_is_a_safe_503(monkeypatch, path):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 296000.5")
    monkeypatch.setattr(router_module, "fetch_copper_zm", boom)
    response = client.get(path)
    assert response.status_code == 503 and "SECRET" not in response.text and "296000" not in response.text


def test_zm_routes_never_reach_an_upstream_source(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("COPPER ZM must read PostgreSQL only")
    import httpx
    from app.services import imf_pcps, worldbank_oil
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "stream", no_network)
    monkeypatch.setattr(imf_pcps, "fetch_copper_payload", no_network)
    monkeypatch.setattr(worldbank_oil, "download_workbook", no_network)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_network)
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: ([month()], 0))
    assert client.get("/api/v1/copper/zm/latest").status_code == 200 and client.get("/api/v1/copper/zm/history").status_code == 200


def test_the_usd_copper_routes_are_undisturbed(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_copper_zm", lambda r, today: ([month()], 0))
    monkeypatch.setattr(router_module, "fetch_latest_copper", lambda today: None)
    assert client.get("/api/v1/copper/latest").json() == {"observation": None}
    assert client.get("/api/v1/copper/zm/latest").json()["classification"] == "derived"


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
        c.execute(text("DROP TABLE IF EXISTS fx_rates"))
        c.execute(text("DROP TABLE IF EXISTS global_copper_prices"))
        for name in ("001_schema.sql", "002_fx_rates_provenance.sql", "011_global_copper_prices.sql"):
            c.execute(text((init / name).read_text()))
        c.execute(text("DELETE FROM fx_rates"))
    yield eng
    eng.dispose()


def put_fx(engine, rows, *, source="boz", pair=("USD", "ZMW"), buy_sell=True):
    with engine.begin() as c:
        for moment, mid in rows:
            c.execute(text("INSERT INTO fx_rates (base_currency, quote_currency, observed_at, buy, mid, sell, source) "
                           "VALUES (:b, :q, :t, :buy, :mid, :sell, :s)"),
                      {"b": pair[0], "q": pair[1], "t": utc(moment), "mid": Decimal(mid), "s": source,
                       "buy": Decimal(mid) - Decimal("0.01") if buy_sell else None, "sell": Decimal(mid) + Decimal("0.01") if buy_sell else None})


def put_copper(engine, rows):
    save_copper_months([CopperMonth(date(y, m, 1), Decimal(v)) for (y, m), v in rows], source_url=IMF_COPPER_URL,
                       retrieved_at=RETRIEVED, engine=engine)


def test_pg_stored_copper_and_fx_derive_exact_equal_weight_months(engine):
    put_copper(engine, [((2026, 10), "14507.97363636364")])
    put_fx(engine, [("2026-10-01T07:30:00Z", "22.4000"), ("2026-10-01T10:30:00Z", "22.4000"), ("2026-10-01T13:30:00Z", "22.4000"),
                    ("2026-10-02T07:30:00Z", "22.6000")])
    (m,), omitted = copper_zm.fetch_copper_zm("ALL", today=TODAY, engine=engine)
    assert omitted == 0 and m.usd_zmw_observed_days_avg == Decimal("22.5")
    assert m.zmw_per_mt == Decimal("14507.97363636364") * Decimal("22.5")
    assert (m.fx_day_count, m.fx_observation_count, m.fx_coverage) == (2, 4, "observed_days")
    assert (m.copper_source_url, m.copper_retrieved_at, m.copper_source_definition) == (IMF_COPPER_URL, RETRIEVED, COPPER_SOURCE_DEFINITION)
    assert isinstance(m.zmw_per_mt, Decimal)


def test_pg_omission_partial_window_and_range_filtering(engine):
    put_copper(engine, [((2025, 11), "9000"), ((2025, 12), "9100"), ((2026, 1), "9200"), ((2026, 2), "9300"), ((2026, 10), "14500")])
    put_fx(engine, [("2026-01-22T07:30:00Z", "19.6"), ("2026-01-23T07:30:00Z", "19.8"), ("2026-10-05T07:30:00Z", "22")])
    months, omitted = copper_zm.fetch_copper_zm("ALL", today=TODAY, engine=engine)
    assert [(m.period_start, m.fx_coverage) for m in months] == [(date(2026, 1, 1), "partial_source_window"), (date(2026, 10, 1), "observed_days")]
    assert omitted == 3  # Nov, Dec 2025 predate stored BoZ; Feb 2026 has no stored FX
    one_year, omitted_1y = copper_zm.fetch_copper_zm("1Y", today=TODAY, engine=engine)  # Copper from 2025-11-01
    assert [m.period_start for m in one_year] == [date(2026, 1, 1), date(2026, 10, 1)] and omitted_1y == 3
    assert one_year[0].fx_coverage == "partial_source_window"  # the full FX window is read even when the range is narrow
    assert months[0].usd_zmw_observed_days_avg == Decimal("19.7")


def test_pg_only_the_boz_usdzmw_complete_population_is_used(engine):
    put_copper(engine, [((2026, 3), "100")])
    put_fx(engine, [("2026-03-02T07:30:00Z", "20")])
    put_fx(engine, [("2026-03-03T07:30:00Z", "99")], source="other")
    put_fx(engine, [("2026-03-04T07:30:00Z", "88")], pair=("EUR", "ZMW"))
    put_fx(engine, [("2026-03-05T07:30:00Z", "77")], buy_sell=False)
    (m,), _ = copper_zm.fetch_copper_zm("ALL", today=TODAY, engine=engine)
    assert (m.usd_zmw_observed_days_avg, m.fx_day_count, m.fx_observation_count) == (Decimal("20"), 1, 1)


def test_pg_zero_and_negative_stored_copper_derive_exactly(engine):
    put_copper(engine, [((2026, 3), "0"), ((2026, 4), "-12.5")])
    put_fx(engine, [("2026-03-02T07:30:00Z", "20"), ("2026-04-02T07:30:00Z", "20")])
    months, _ = copper_zm.fetch_copper_zm("ALL", today=TODAY, engine=engine)
    assert [m.zmw_per_mt for m in months] == [Decimal("0"), Decimal("-250")]


def test_pg_derivation_is_read_only_and_persists_nothing(engine):
    put_copper(engine, [((2026, 3), "100")])
    put_fx(engine, [("2026-03-02T07:30:00Z", "20")])
    def snapshot():
        with engine.connect() as c:
            tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
            counts = {t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in tables}
        return tables, counts
    before = snapshot()
    copper_zm.fetch_copper_zm("ALL", today=TODAY, engine=engine); copper_zm.fetch_copper_zm("1Y", today=TODAY, engine=engine)
    assert snapshot() == before
    assert not [t for t in before[0] if "zm" in t and "copper" in t]  # no derived table exists
    assert not list(Path(__file__).parents[3].joinpath("db/init").glob("012*"))  # no migration 012
