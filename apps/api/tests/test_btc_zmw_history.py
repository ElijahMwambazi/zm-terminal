import importlib
import inspect
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.btc import BTC_ZMW_DAILY_METHODOLOGY_ID, BTC_ZMW_DAILY_NOTICE, BTC_ZMW_METHODOLOGY_ID, BtcDailyCandle
from app.services import btc_zmw_history
from app.services.btc_store import save_daily_candles
from app.services.bitstamp_btc import DailyCandle
from app.services.btc_zmw_history import InvalidHistoryInputError, derive_btc_zmw_history, fetch_btc_zmw_history, lusaka_date

router_module = importlib.import_module("app.api.router")
UTC = timezone.utc
D0 = datetime(2026, 1, 22, tzinfo=UTC)  # a Thursday


def candle(day, close="70000.00", **kw):
    return BtcDailyCandle(candle_open_at=D0 + timedelta(days=day), open=Decimal(close), high=Decimal(close), low=Decimal(close),
                          close=Decimal(close), volume_btc=Decimal("1"), **kw)


def fx(day, hour, minute, mid):  # a UTC instant on the n-th day after 2026-01-22
    return (D0 + timedelta(days=day, hours=hour, minutes=minute), Decimal(mid))


def derive(candles, observations):
    return derive_btc_zmw_history(candles, observations)


# ---------------- pure derivation ----------------
def test_one_candle_and_one_observation():
    points, omitted = derive([candle(0, "70000.00")], [fx(0, 7, 30, "20.0000")])
    (p,) = points
    assert omitted == 0 and p.date == date(2026, 1, 22)
    assert p.btc_zmw == Decimal("1400000.000000") and p.btc_usd_close == Decimal("70000.00") and p.usd_zmw_daily_mid_avg == Decimal("20.0000")
    assert (p.classification, p.derived, p.currency, p.unit) == ("derived", True, "ZMW", "bitcoin")
    assert (p.btc_source, p.btc_market, p.btc_price_type) == ("bitstamp", "BTC/USD", "daily_close")
    assert (p.fx_source, p.fx_pair, p.fx_price) == ("boz", "USDZMW", "daily_mid_average")
    assert p.fx_observation_count == 1 and p.fx_first_observation == p.fx_last_observation == D0 + timedelta(hours=7, minutes=30)
    assert p.btc_candle_open_at == D0


def test_multiple_observations_use_the_equal_weight_arithmetic_mean_with_first_and_last_timestamps():
    obs = [fx(0, 13, 30, "20.4"), fx(0, 7, 30, "20.0"), fx(0, 10, 30, "20.2")]  # unordered input
    (p,), _ = derive([candle(0, "100")], obs)
    assert p.usd_zmw_daily_mid_avg == Decimal("20.2") and p.btc_zmw == Decimal("2020.0") and p.fx_observation_count == 3
    assert p.fx_first_observation == D0 + timedelta(hours=7, minutes=30) and p.fx_last_observation == D0 + timedelta(hours=13, minutes=30)
    (q,), _ = derive([candle(0, "100")], [fx(0, 7, 30, "20"), fx(0, 10, 30, "20"), fx(0, 13, 30, "23")])  # not time/slot weighted
    assert q.usd_zmw_daily_mid_avg == Decimal("21")


def test_exact_decimal_multiplication_without_rounding_at_forty_digits():
    (p,), _ = derive([candle(0, "67112.40")], [fx(0, 7, 30, "20.4451")])
    assert p.btc_zmw == Decimal("1372119.729240") and isinstance(p.btc_zmw, Decimal)
    (p,), _ = derive([candle(0, "123456789.123456789")], [fx(0, 7, 0, "98765.4321987654")])
    with localcontext() as context:
        context.prec = 60
        assert p.btc_zmw == Decimal("123456789.123456789") * Decimal("98765.4321987654")  # 33 digits are exact at 40
    (p,), _ = derive([candle(0, "1")], [fx(0, 1, 0, "1"), fx(0, 2, 0, "1"), fx(0, 3, 0, "2")])
    with localcontext() as context:
        context.prec = 40
        assert p.usd_zmw_daily_mid_avg == Decimal("4") / Decimal("3")  # a 40-digit mean, not rounded to display precision
    assert str(p.usd_zmw_daily_mid_avg).startswith("1.3333333333") and len(str(p.usd_zmw_daily_mid_avg)) > 30


def test_a_btc_day_without_fx_is_omitted_and_counted_never_filled():
    candles = [candle(i) for i in range(7)]  # Thu 22 Jan .. Wed 28 Jan; Sat/Sun have no BoZ observations
    obs = [fx(0, 7, 30, "20"), fx(1, 7, 30, "21"), fx(4, 7, 30, "22"), fx(5, 7, 30, "23")]  # Thu, Fri, Mon 26... see below
    points, omitted = derive(candles, obs)
    assert [p.date for p in points] == [date(2026, 1, 22), date(2026, 1, 23), date(2026, 1, 26), date(2026, 1, 27)]
    assert omitted == 3  # the weekend (24 and 25 Jan) and 28 Jan
    by_date = {p.date: p.usd_zmw_daily_mid_avg for p in points}
    assert date(2026, 1, 24) not in by_date and date(2026, 1, 25) not in by_date  # Friday's rate is not carried over the weekend
    assert by_date[date(2026, 1, 26)] == Decimal("22") and by_date[date(2026, 1, 27)] == Decimal("23")  # each day its own value: no interpolation


def test_consecutive_missing_days_and_fx_days_without_a_candle():
    points, omitted = derive([candle(0), candle(5)], [fx(0, 7, 30, "20"), fx(3, 7, 30, "99"), fx(5, 7, 30, "21")])
    assert [p.date for p in points] == [date(2026, 1, 22), date(2026, 1, 27)] and omitted == 0  # the FX day 25 Jan without a candle is irrelevant
    points, omitted = derive([candle(1), candle(2), candle(3)], [fx(0, 7, 30, "20")])
    assert points == [] and omitted == 3  # no nearest-observation substitution
    assert derive([], [fx(0, 7, 30, "20")]) == ([], 0) and derive([candle(0)], []) == ([], 1)


def test_no_nulls_zeros_or_backfill_in_the_output():
    points, _ = derive([candle(i) for i in range(4)], [fx(1, 7, 30, "20"), fx(3, 7, 30, "21")])
    assert [p.date for p in points] == [date(2026, 1, 23), date(2026, 1, 25)]
    assert all(p.btc_zmw > 0 and p.usd_zmw_daily_mid_avg > 0 for p in points)


def test_output_is_ascending_and_input_order_does_not_matter():
    candles = [candle(2), candle(0), candle(1)]
    obs = [fx(2, 7, 30, "22"), fx(0, 7, 30, "20"), fx(1, 7, 30, "21")]
    points, _ = derive(candles, obs)
    assert [p.date for p in points] == [date(2026, 1, 22), date(2026, 1, 23), date(2026, 1, 24)]
    assert points == derive(list(reversed(candles)), list(reversed(obs)))[0]


def test_boz_observations_are_assigned_to_the_africa_lusaka_calendar_date():
    late = datetime(2026, 1, 22, 23, 30, tzinfo=UTC)  # 2026-01-23 01:30 in Lusaka
    assert lusaka_date(late) == date(2026, 1, 23)
    assert lusaka_date(datetime(2026, 1, 22, 21, 59, tzinfo=UTC)) == date(2026, 1, 22)
    assert lusaka_date(datetime(2026, 1, 22, 22, 0, tzinfo=UTC)) == date(2026, 1, 23)  # Lusaka midnight
    assert lusaka_date(datetime(2026, 1, 22, 23, 30, tzinfo=timezone(timedelta(hours=-5)))) == date(2026, 1, 23)  # offsets are honoured
    points, omitted = derive([candle(0), candle(1)], [(late, Decimal("25"))])
    assert [p.date for p in points] == [date(2026, 1, 23)] and omitted == 1  # matches the 23 Jan candle, not 22 Jan
    assert points[0].fx_first_observation == late
    with pytest.raises(InvalidHistoryInputError):
        lusaka_date(datetime(2026, 1, 22, 7, 30))  # a naive timestamp is refused, never guessed


def test_the_utc_candle_date_is_the_candles_own_utc_date():
    near_midnight = BtcDailyCandle(candle_open_at=datetime(2026, 1, 22, 0, 0, tzinfo=UTC), open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(5), volume_btc=Decimal(0))
    points, _ = derive([near_midnight], [(datetime(2026, 1, 22, 7, 30, tzinfo=UTC), Decimal(2))])
    assert points[0].date == date(2026, 1, 22) and points[0].btc_zmw == Decimal(10) and points[0].btc_candle_open_at == near_midnight.candle_open_at


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_btc_close_or_fx_mid_fails_safely(bad):
    with pytest.raises(InvalidHistoryInputError):
        derive([candle(0, "70000")], [(D0 + timedelta(hours=7), Decimal(bad))])
    with pytest.raises(InvalidHistoryInputError):
        derive([BtcDailyCandle.model_construct(candle_open_at=D0, open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(bad), volume_btc=Decimal(0))],
               [fx(0, 7, 30, "20")])


def test_non_simultaneity_is_stated_in_the_metadata_and_methodology_ids_are_separate():
    assert "not simultaneous" in BTC_ZMW_DAILY_NOTICE and "not an executable BTC/ZMW market price" in BTC_ZMW_DAILY_NOTICE
    assert BTC_ZMW_DAILY_METHODOLOGY_ID == "btc_zmw_daily_v1" != BTC_ZMW_METHODOLOGY_ID == "btc_zmw_live_v1"
    for forbidden in ("synchronized", "official", "closing price"):
        assert forbidden not in BTC_ZMW_DAILY_NOTICE.replace("not an executable BTC/ZMW market price", "")


def test_the_module_is_pure_storage_arithmetic():
    source = inspect.getsource(btc_zmw_history)
    for forbidden in ("httpx", "get_bitstamp", "get_boz", "INSERT", "UPDATE ", "CREATE TABLE", "float(", "round("):
        assert forbidden not in source
    assert "fetch_latest_fx_mid" not in source  # the history needs the full daily population, not the newest row


# ---------------- API (offline) ----------------
client = TestClient(app)


def sample_history(range_="1Y"):
    points, omitted = derive([candle(0, "70000"), candle(1, "71000")], [fx(0, 7, 30, "20"), fx(0, 10, 30, "21")])
    from app.schemas.btc import BtcZmwHistory
    return BtcZmwHistory(range=range_, count=len(points), btc_days_without_fx=omitted, fx_stored_from=date(2026, 1, 22), observations=points)


def test_history_route_returns_the_derived_series_and_passes_the_range(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_btc_zmw_history", lambda r, now: seen.append(r) or sample_history(r))
    body = client.get("/api/v1/btc/zmw/history?range=3Y").json()
    assert seen == ["3Y"] and body["range"] == "3Y" and body["count"] == 1 and body["btc_days_without_fx"] == 1
    assert (body["classification"], body["derived"], body["methodology_id"], body["fx_stored_from"]) == ("derived", True, "btc_zmw_daily_v1", "2026-01-22")
    point = body["observations"][0]
    assert (point["date"], point["btc_zmw"], point["btc_usd_close"], point["usd_zmw_daily_mid_avg"], point["fx_observation_count"]) == (
        "2026-01-22", "1435000.0", "70000", "20.5", 2)
    assert point["btc_price_type"] == "daily_close" and point["fx_price"] == "daily_mid_average" and "stale" not in point
    assert client.get("/api/v1/btc/zmw/history").status_code == 200 and seen[-1] == "1Y"
    assert client.get("/api/v1/btc/zmw/history?range=2W").status_code == 422


def test_history_route_empty_result_is_a_valid_response_not_a_404(monkeypatch):
    from app.schemas.btc import BtcZmwHistory
    monkeypatch.setattr(router_module, "fetch_btc_zmw_history", lambda r, now: BtcZmwHistory(range=r, count=0, btc_days_without_fx=30, fx_stored_from=None, observations=[]))
    r = client.get("/api/v1/btc/zmw/history?range=1M")
    assert r.status_code == 200 and r.json()["count"] == 0 and r.json()["observations"] == [] and r.json()["fx_stored_from"] is None


def test_history_route_failures_are_safe_503s(monkeypatch):
    def boom(r, now):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_btc_zmw_history", boom)
    r = client.get("/api/v1/btc/zmw/history?range=1M")
    assert r.status_code == 503 and "SECRET" not in r.text

    def bad(r, now):
        raise InvalidHistoryInputError("Stored USD/ZMW mid is not a positive finite number 0")
    monkeypatch.setattr(router_module, "fetch_btc_zmw_history", bad)
    r = client.get("/api/v1/btc/zmw/history?range=1M")
    assert r.status_code == 503 and r.json()["detail"] == "A stored BTC/USD or USD/ZMW value is invalid"


def test_history_makes_no_upstream_calls_and_the_live_route_is_unaffected(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("BTC/ZMW history must read PostgreSQL only")
    monkeypatch.setattr(router_module, "get_bitstamp_btc_quote", no_network)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)  # any outbound async HTTP from the app would fail the test
    monkeypatch.setattr(router_module, "fetch_btc_zmw_history", lambda r, now: sample_history(r))
    assert client.get("/api/v1/btc/zmw/history?range=ALL").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/btc/zmw/latest" in paths and "/api/v1/btc/zmw/history" in paths  # no route collision


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
        c.execute(text("DROP TABLE IF EXISTS btc_usd_daily"))
        c.execute(text("DROP TABLE IF EXISTS fx_rates"))
        c.execute(text((init / "001_schema.sql").read_text()))
        c.execute(text((init / "002_fx_rates_provenance.sql").read_text()))
        c.execute(text((init / "010_btc_usd_daily.sql").read_text()))
        c.execute(text("DELETE FROM fx_rates"))
    yield eng
    eng.dispose()


def put_candle(engine, n, close):
    c = Decimal(close)
    save_daily_candles([DailyCandle(D0 + timedelta(days=n), c, c, c, c, Decimal("1"))], retrieved_at=D0, engine=engine)


def put_fx(engine, observed, mid, *, base="USD", quote="ZMW", source="boz", buy="x", sell="x"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO fx_rates (base_currency, quote_currency, observed_at, buy, mid, sell, source, retrieved_at, source_url) "
                       "VALUES (:b, :q, :o, :buy, :mid, :sell, :s, :o, 'u')"),
                  {"b": base, "q": quote, "o": observed, "buy": mid if buy == "x" else buy, "mid": mid, "sell": mid if sell == "x" else sell, "s": source})


NOW = datetime(2026, 2, 5, 12, tzinfo=UTC)


def test_pg_history_selects_only_complete_boz_usd_zmw_rows_and_stored_bitstamp_daily_candles(engine):
    for n, close in ((0, "70000"), (1, "71000"), (2, "72000"), (5, "73000")):
        put_candle(engine, n, close)
    put_fx(engine, D0 + timedelta(hours=7, minutes=30), "20.0")
    put_fx(engine, D0 + timedelta(hours=10, minutes=30), "22.0")
    put_fx(engine, D0 + timedelta(hours=5), "99.0", base="EUR", quote="USD")                 # wrong pair
    put_fx(engine, D0 + timedelta(hours=6), "98.0", source="other")                            # wrong source
    put_fx(engine, D0 + timedelta(hours=8), "97.0", sell=None)                                 # incomplete
    put_fx(engine, D0 + timedelta(hours=9), "96.0", buy=None)
    put_fx(engine, D0 + timedelta(days=1, hours=7, minutes=30), "30.0", source="other")        # a day only a wrong source covers
    put_fx(engine, D0 + timedelta(days=2, hours=7, minutes=30), "25.0")
    result = fetch_btc_zmw_history("ALL", now=NOW, engine=engine)
    assert [p.date for p in result.observations] == [date(2026, 1, 22), date(2026, 1, 24)]
    first = result.observations[0]
    assert first.usd_zmw_daily_mid_avg == Decimal("21.0") and first.fx_observation_count == 2 and first.btc_zmw == Decimal("1470000.00")
    assert result.observations[1].btc_zmw == Decimal("1800000") and result.btc_days_without_fx == 2  # 23 Jan and 27 Jan have no eligible FX
    assert result.count == 2 and result.fx_stored_from == date(2026, 1, 22)
    with pytest.raises(Exception):  # another BTC provider/market/interval cannot exist: the table's checks forbid it
        with engine.begin() as c:
            c.execute(text("INSERT INTO btc_usd_daily (provider, market, interval, candle_open_at, open, high, low, close, volume_btc, source_url, retrieved_at) "
                           "VALUES ('other','BTC/USD','1d', :t, 1, 1, 1, 1, 1, 'u', now())"), {"t": D0})


def test_pg_timezone_boundary_uses_the_lusaka_date(engine):
    put_candle(engine, 0, "70000")  # 22 Jan
    put_candle(engine, 1, "71000")  # 23 Jan
    put_fx(engine, datetime(2026, 1, 22, 23, 30, tzinfo=UTC), "25.0")  # 23 Jan 01:30 in Lusaka
    result = fetch_btc_zmw_history("ALL", now=NOW, engine=engine)
    assert [p.date for p in result.observations] == [date(2026, 1, 23)] and result.btc_days_without_fx == 1
    assert result.observations[0].btc_zmw == Decimal("1775000.0")
    put_fx(engine, datetime(2026, 1, 22, 21, 59, tzinfo=UTC), "24.0")  # 22 Jan 23:59 in Lusaka
    result = fetch_btc_zmw_history("ALL", now=NOW, engine=engine)
    assert [p.date for p in result.observations] == [date(2026, 1, 22), date(2026, 1, 23)] and result.btc_days_without_fx == 0


def test_pg_range_bounds_candles_and_fx_and_the_boundary_is_not_cut(engine):
    for n in range(40):
        put_candle(engine, n, "70000")
        put_fx(engine, D0 + timedelta(days=n, hours=7, minutes=30), "20")
    now = D0 + timedelta(days=39, hours=15)
    month = fetch_btc_zmw_history("1M", now=now, engine=engine)
    # BTC history semantics: the window starts at the UTC midnight of (now - 30 days), so that whole day is included
    assert month.observations[0].date == (now - timedelta(days=30)).date() == date(2026, 1, 31) and month.count == 31
    assert month.btc_days_without_fx == 0 and month.observations[-1].date == date(2026, 3, 2)
    assert fetch_btc_zmw_history("ALL", now=now, engine=engine).count == 40
    assert fetch_btc_zmw_history("1M", now=D0 + timedelta(days=400), engine=engine).count == 0  # nothing in range: a valid empty history


def test_pg_empty_tables_and_read_only(engine):
    empty = fetch_btc_zmw_history("ALL", now=NOW, engine=engine)
    assert (empty.count, empty.btc_days_without_fx, empty.fx_stored_from, empty.observations) == (0, 0, None, [])
    put_candle(engine, 0, "70000")
    put_fx(engine, D0 + timedelta(hours=7), "20")
    with engine.connect() as c:
        before = (c.execute(text("SELECT count(*) FROM btc_usd_daily")).scalar(), c.execute(text("SELECT count(*) FROM fx_rates")).scalar(),
                  sorted(r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))))
    fetch_btc_zmw_history("ALL", now=NOW, engine=engine)
    fetch_btc_zmw_history("1M", now=NOW, engine=engine)
    with engine.connect() as c:
        after = (c.execute(text("SELECT count(*) FROM btc_usd_daily")).scalar(), c.execute(text("SELECT count(*) FROM fx_rates")).scalar(),
                 sorted(r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))))
    assert before == after and not [t for t in after[2] if "zmw" in t]  # nothing derived is persisted and no BTC/ZMW table exists
    assert not [f for f in Path(__file__).parents[3].glob("db/init/*") if "zmw" in f.name.lower() and "btc" in f.name.lower()]


# ---------------- PostgreSQL: range-limited counts, FX timezone bounds, global fx_stored_from ----------------
def put_candle_on(engine, day, close="70000"):
    c = Decimal(close)
    save_daily_candles([DailyCandle(datetime(day.year, day.month, day.day, tzinfo=UTC), c, c, c, c, Decimal("1"))], retrieved_at=D0, engine=engine)


def test_pg_omission_count_covers_only_the_selected_btc_dates_in_the_range(engine):
    outside = [date(2026, 1, 25), date(2026, 1, 28), date(2026, 2, 1)]          # before the 1M window
    inside = [date(2026, 2, 20), date(2026, 2, 25), date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 5)]
    for d in outside + inside:
        put_candle_on(engine, d)
    put_fx(engine, datetime(2026, 1, 28, 7, 30, tzinfo=UTC), "19")                    # eligible FX, but its BTC date is outside the range
    put_fx(engine, datetime(2026, 2, 22, 7, 30, tzinfo=UTC), "18")                    # an FX-only date: no BTC candle
    put_fx(engine, datetime(2026, 2, 25, 7, 30, tzinfo=UTC), "17", source="other")    # ineligible FX on a selected BTC date
    put_fx(engine, datetime(2026, 3, 3, 7, 30, tzinfo=UTC), "20")                     # the only eligible FX on a selected BTC date
    now = datetime(2026, 3, 6, 12, tzinfo=UTC)                                         # 1M window starts at 2026-02-04 00:00 UTC
    month = fetch_btc_zmw_history("1M", now=now, engine=engine)
    assert [p.date for p in month.observations] == [date(2026, 3, 3)]
    assert (month.count, month.btc_days_without_fx) == (1, 4)                          # not 7: dates outside the range and FX-only dates are not counted
    assert month.count + month.btc_days_without_fx == len(inside)                      # selected BTC candles = derived + omitted
    everything = fetch_btc_zmw_history("ALL", now=now, engine=engine)
    assert [p.date for p in everything.observations] == [date(2026, 1, 28), date(2026, 3, 3)]
    assert (everything.count, everything.btc_days_without_fx) == (2, 6) and everything.count + everything.btc_days_without_fx == len(inside + outside)


def _single_candle_fixture(engine):
    put_candle_on(engine, date(2026, 3, 5), "70000")


def test_pg_fx_lower_bound_includes_lusaka_midnight_and_excludes_the_previous_lusaka_date(engine):
    _single_candle_fixture(engine)
    put_fx(engine, datetime(2026, 3, 4, 21, 59, tzinfo=UTC), "98")  # Lusaka 2026-03-04 23:59: not 5 March
    put_fx(engine, datetime(2026, 3, 4, 22, 0, tzinfo=UTC), "21")    # Lusaka 2026-03-05 00:00 although its UTC date is 4 March
    result = fetch_btc_zmw_history("ALL", now=datetime(2026, 3, 6, tzinfo=UTC), engine=engine)
    (p,) = result.observations
    assert p.date == date(2026, 3, 5) and p.usd_zmw_daily_mid_avg == Decimal("21") and p.fx_observation_count == 1
    assert p.fx_first_observation == datetime(2026, 3, 4, 22, 0, tzinfo=UTC) and p.btc_zmw == Decimal("1470000")


def test_pg_fx_upper_bound_includes_the_last_minute_and_excludes_the_next_lusaka_date(engine):
    _single_candle_fixture(engine)
    put_fx(engine, datetime(2026, 3, 5, 21, 59, tzinfo=UTC), "22")   # Lusaka 2026-03-05 23:59
    put_fx(engine, datetime(2026, 3, 5, 22, 0, tzinfo=UTC), "99")    # Lusaka 2026-03-06 00:00
    result = fetch_btc_zmw_history("ALL", now=datetime(2026, 3, 6, tzinfo=UTC), engine=engine)
    (p,) = result.observations
    assert p.usd_zmw_daily_mid_avg == Decimal("22") and p.fx_observation_count == 1 and p.fx_last_observation == datetime(2026, 3, 5, 21, 59, tzinfo=UTC)


def test_pg_fx_both_edges_together_average_only_the_included_observations(engine):
    _single_candle_fixture(engine)
    for stamp, mid in ((datetime(2026, 3, 4, 21, 59, tzinfo=UTC), "98"), (datetime(2026, 3, 4, 22, 0, tzinfo=UTC), "21"),
                       (datetime(2026, 3, 5, 21, 59, tzinfo=UTC), "22"), (datetime(2026, 3, 5, 22, 0, tzinfo=UTC), "99")):
        put_fx(engine, stamp, mid)
    (p,) = fetch_btc_zmw_history("ALL", now=datetime(2026, 3, 6, tzinfo=UTC), engine=engine).observations
    assert p.usd_zmw_daily_mid_avg == Decimal("21.5") and p.fx_observation_count == 2 and p.btc_zmw == Decimal("1505000.0")
    assert (p.fx_first_observation, p.fx_last_observation) == (datetime(2026, 3, 4, 22, 0, tzinfo=UTC), datetime(2026, 3, 5, 21, 59, tzinfo=UTC))


def test_pg_the_sql_window_is_exactly_the_lusaka_calendar_day_of_the_selected_dates(engine):
    from app.services.btc_zmw_history import _lusaka_midnight_utc
    from app.services.fx_store import fetch_boz_mid_observations
    lo, hi = _lusaka_midnight_utc(date(2026, 3, 5)), _lusaka_midnight_utc(date(2026, 3, 6))
    assert (lo, hi) == (datetime(2026, 3, 4, 22, 0, tzinfo=UTC), datetime(2026, 3, 5, 22, 0, tzinfo=UTC))
    for stamp, mid in ((datetime(2026, 3, 4, 21, 59, tzinfo=UTC), "98"), (lo, "21"), (datetime(2026, 3, 5, 12, tzinfo=UTC), "23"),
                       (hi - timedelta(minutes=1), "22"), (hi, "99")):
        put_fx(engine, stamp, mid)
    rows = fetch_boz_mid_observations(lo, hi, engine=engine)  # the real SQL: since inclusive, until exclusive, ascending
    assert [str(m) for _, m in rows] == ["21", "23", "22"]


def test_pg_fx_stored_from_is_the_global_eligible_boundary_not_range_local(engine):
    put_fx(engine, datetime(2026, 1, 10, 7, 30, tzinfo=UTC), "50", base="EUR", quote="USD")   # earlier but the wrong pair
    put_fx(engine, datetime(2026, 1, 12, 7, 30, tzinfo=UTC), "51", source="other")             # earlier but the wrong provider
    put_fx(engine, datetime(2026, 1, 15, 7, 30, tzinfo=UTC), "52", sell=None)                  # earlier but incomplete
    put_fx(engine, datetime(2026, 1, 22, 7, 30, tzinfo=UTC), "19")                             # the earliest ELIGIBLE row
    for d in (date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)):
        put_candle_on(engine, d)
    put_fx(engine, datetime(2026, 11, 11, 7, 30, tzinfo=UTC), "20.5")
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    month = fetch_btc_zmw_history("1M", now=now, engine=engine)
    assert [p.date for p in month.observations] == [date(2026, 11, 11)] and month.btc_days_without_fx == 2  # range-limited
    assert month.fx_stored_from == date(2026, 1, 22)                                                      # global, and untouched by the distractors
    assert fetch_btc_zmw_history("ALL", now=now, engine=engine).fx_stored_from == date(2026, 1, 22)
    empty_range = fetch_btc_zmw_history("1M", now=datetime(2027, 6, 1, tzinfo=UTC), engine=engine)
    assert empty_range.count == 0 and empty_range.fx_stored_from == date(2026, 1, 22)
