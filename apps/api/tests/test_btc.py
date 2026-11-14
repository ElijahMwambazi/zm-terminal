import asyncio
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
from app.schemas.btc import BITSTAMP_OHLC_URL, BITSTAMP_TICKER_URL, BTC_ZMW_NOTICE
from app.services import bitstamp_btc, btc_zmw as btc_zmw_module
from app.services.bitstamp_btc import (
    BitstampError, DailyCandle, fetch_daily_candles, get_bitstamp_btc_quote, is_btc_stale, parse_candle, parse_ohlc, parse_ticker,
)
from app.services.btc_store import BtcConflictError, fetch_btc_history, range_start, save_daily_candles
from app.services.btc_zmw import derive_btc_zmw
from app.services.fx_store import LatestFxMid
from app.services.provider_policy import BITSTAMP_BTC_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
UTC = timezone.utc
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
TS = str(int(datetime(2026, 11, 14, 11, 59, 30, tzinfo=UTC).timestamp()))


def ticker(**over):
    body = {"timestamp": TS, "open": "68227.80", "high": "70130.34", "low": "66397.36", "last": "67185.23", "volume": "1156.58547707",
            "vwap": "68238.65", "bid": "67187.23", "ask": "67187.24", "side": "1", "open_24": "69240.36", "percent_change_24": "-2.97",
            "market_type": "SPOT"}
    body.update(over)
    return {k: v for k, v in body.items() if v is not None}


def day(n):  # n-th UTC day after 2026-01-01
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=n)


def candle_row(n, o="100.00", h="110.00", l="95.00", c="105.00", v="12.50000000"):
    return {"timestamp": str(int(day(n).timestamp())), "open": o, "high": h, "low": l, "close": c, "volume": v}


def envelope(rows, pair="BTC/USD"):
    return {"data": {"pair": pair, "ohlc": rows}}


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# ---------------- live ticker parsing ----------------
def test_valid_ticker_uses_last_as_decimal_and_converts_the_timestamp():
    q = parse_ticker(ticker(), retrieved_at=NOW)
    assert q.last == Decimal("67185.23") and isinstance(q.last, Decimal)
    assert q.observed_at == datetime(2026, 11, 14, 11, 59, 30, tzinfo=UTC) and q.observed_at.utcoffset() == timedelta(0)
    assert (q.symbol, q.market, q.market_type, q.price_type, q.source) == ("BTCUSD", "BTC/USD", "SPOT", "last_trade", "bitstamp")
    assert (q.bid, q.ask, q.high_24h, q.low_24h, q.vwap_24h, q.volume_24h_btc) == (
        Decimal("67187.23"), Decimal("67187.24"), Decimal("70130.34"), Decimal("66397.36"), Decimal("68238.65"), Decimal("1156.58547707"))
    assert q.source_url == BITSTAMP_TICKER_URL and q.stale is False and q.stale_after_seconds == 300


def test_headline_price_is_last_not_bid_ask_vwap_or_mark():
    q = parse_ticker(ticker(last="100.5", bid="90", ask="110", vwap="99", mark_price="5", index_price="6"), retrieved_at=NOW)
    assert q.last == Decimal("100.5")
    assert "mark_price" not in q.model_dump() and "open_24" not in q.model_dump() and "percent_change_24" not in q.model_dump()


@pytest.mark.parametrize("payload", [[ticker()], [], "BTC", None, 5])
def test_non_object_bodies_including_the_all_markets_array_are_rejected(payload):
    with pytest.raises(BitstampError):
        parse_ticker(payload, retrieved_at=NOW)


@pytest.mark.parametrize("missing", ["last", "timestamp", "market_type", "bid", "ask", "high", "low", "vwap", "volume"])
def test_missing_required_fields_are_rejected(missing):
    with pytest.raises(BitstampError):
        parse_ticker(ticker(**{missing: None}), retrieved_at=NOW)


def test_market_type_must_be_spot():
    for bad in ("PERPETUAL", "spot", None, 1):
        with pytest.raises(BitstampError):
            parse_ticker({**ticker(), "market_type": bad}, retrieved_at=NOW)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "1e5", "", "abc", "1,000", " 5", 5, 5.5, None, "0"])
def test_invalid_or_non_positive_decimal_last_is_rejected(bad):
    with pytest.raises(BitstampError):
        parse_ticker({**ticker(), "last": bad}, retrieved_at=NOW)


def test_zero_24h_volume_is_allowed_but_zero_price_fields_are_not():
    assert parse_ticker(ticker(volume="0.00000000"), retrieved_at=NOW).volume_24h_btc == 0
    with pytest.raises(BitstampError):
        parse_ticker(ticker(bid="0"), retrieved_at=NOW)


@pytest.mark.parametrize("bad", ["abc", "-5", "1.5", "", 1794656512, None, "9" * 13])
def test_invalid_timestamp_is_rejected(bad):
    with pytest.raises(BitstampError):
        parse_ticker({**ticker(), "timestamp": bad}, retrieved_at=NOW)


def test_future_timestamp_is_rejected_beyond_clock_tolerance_but_small_skew_is_accepted():
    far = str(int((NOW + timedelta(minutes=10)).timestamp()))
    with pytest.raises(BitstampError):
        parse_ticker(ticker(timestamp=far), retrieved_at=NOW)
    skew = str(int((NOW + timedelta(seconds=30)).timestamp()))
    assert parse_ticker(ticker(timestamp=skew), retrieved_at=NOW).stale is False


def test_btc_stale_after_five_minutes_exactly():
    assert is_btc_stale(NOW - timedelta(minutes=5), NOW) is False
    assert is_btc_stale(NOW - timedelta(minutes=5, seconds=1), NOW) is True
    old = str(int((NOW - timedelta(minutes=6)).timestamp()))
    q = parse_ticker(ticker(timestamp=old), retrieved_at=NOW)
    assert q.stale is True and q.stale_after_seconds == 300
    with pytest.raises(ValueError):
        is_btc_stale(datetime(2026, 1, 1), NOW)  # naive timestamps are refused


def test_precision_is_preserved_exactly():
    q = parse_ticker(ticker(last="67185.230", volume="0.123456789012"), retrieved_at=NOW)
    assert str(q.last) == "67185.230" and str(q.volume_24h_btc) == "0.123456789012"


# ---------------- live ticker HTTP behaviour ----------------
def test_quote_request_shape_and_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=ticker(timestamp=str(int(datetime.now(UTC).timestamp()))))
    quote = run(get_bitstamp_btc_quote(client=client_for(handler)))
    (request,) = seen
    assert str(request.url) == BITSTAMP_TICKER_URL and request.method == "GET"
    assert request.headers["accept"] == "application/json" and "ZM-Terminal" in request.headers["user-agent"]
    assert "authorization" not in request.headers and "x-auth" not in request.headers
    assert quote.last == Decimal("67185.23")


def test_http_200_with_the_all_markets_array_is_a_failure_not_a_quote():
    def handler(request):
        return httpx.Response(200, json=[ticker(), ticker(last="1")])
    with pytest.raises(BitstampError):
        run(get_bitstamp_btc_quote(client=client_for(handler)))


def test_http_failure_non_json_and_malformed_json():
    with pytest.raises(httpx.HTTPStatusError):
        run(get_bitstamp_btc_quote(client=client_for(lambda r: httpx.Response(503))))
    with pytest.raises(BitstampError):
        run(get_bitstamp_btc_quote(client=client_for(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))))
    with pytest.raises(ValueError):
        run(get_bitstamp_btc_quote(client=client_for(lambda r: httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}))))


def test_timeout_propagates_and_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(httpx.TimeoutException):
        run(get_bitstamp_btc_quote(client=client_for(handler)))
    assert len(calls) == 1
    assert bitstamp_btc.BITSTAMP_TIMEOUT_SECONDS == 10.0


# ---------------- OHLC candle parsing ----------------
def test_valid_candle_decimals_and_utc_midnight():
    c = parse_candle(candle_row(0, o="8.00", h="8.31", l="5.25", c="8.22", v="164.48000000"))
    assert c.candle_open_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert (c.open, c.high, c.low, c.close, c.volume_btc) == (Decimal("8.00"), Decimal("8.31"), Decimal("5.25"), Decimal("8.22"), Decimal("164.48000000"))
    assert str(c.volume_btc) == "164.48000000" and all(isinstance(x, Decimal) for x in (c.open, c.high, c.low, c.close, c.volume_btc))


def test_zero_volume_candles_are_accepted_including_flat_ones():
    c = parse_candle(candle_row(3, o="8.88", h="8.88", l="8.88", c="8.88", v="0.00000000"))
    assert c.volume_btc == 0 and c.open == c.high == c.low == c.close == Decimal("8.88")
    assert parse_candle(candle_row(3, v="0.00000000")).volume_btc == 0


@pytest.mark.parametrize("kwargs", [
    {"o": "100", "h": "99", "l": "95", "c": "98"},    # open above high
    {"o": "90", "h": "110", "l": "95", "c": "100"},   # open below low
    {"o": "100", "h": "110", "l": "95", "c": "120"},  # close above high
    {"o": "100", "h": "110", "l": "95", "c": "90"},   # close below low
    {"o": "0", "h": "110", "l": "0", "c": "100"},     # zero price
    {"v": "-1"}, {"v": "x"}, {"c": "NaN"}, {"h": "Infinity"},
])
def test_ohlc_invariants_and_bad_numbers_are_rejected(kwargs):
    with pytest.raises(BitstampError):
        parse_candle(candle_row(0, **kwargs))


@pytest.mark.parametrize("ts", ["abc", "-86400", "1767225601", "1767268800", 1767225600, None, ""])
def test_malformed_or_non_midnight_timestamps_are_rejected(ts):
    row = candle_row(0)
    row["timestamp"] = ts
    with pytest.raises(BitstampError):
        parse_candle(row)


def test_ohlc_envelope_rules():
    assert [c.candle_open_at for c in parse_ohlc(envelope([candle_row(0), candle_row(1)]), now=NOW)] == [day(0), day(1)]
    assert parse_ohlc(envelope([]), now=NOW) == []
    for bad in (None, [], {"data": []}, {"data": {"pair": "BTC/USD"}}, {"data": {"ohlc": "x"}}, envelope([candle_row(0)], pair="ETH/USD"), {"code": "x"}):
        with pytest.raises(BitstampError):
            parse_ohlc(bad, now=NOW)
    parse_ohlc({"data": {"ohlc": [candle_row(0)]}}, now=NOW)  # pair is only checked when present


def test_duplicate_or_unordered_candles_are_rejected_not_silently_resolved():
    with pytest.raises(BitstampError):
        parse_ohlc(envelope([candle_row(0), candle_row(0)]), now=NOW)
    with pytest.raises(BitstampError):
        parse_ohlc(envelope([candle_row(1), candle_row(0)]), now=NOW)


def test_the_current_open_candle_is_rejected_even_if_the_server_returned_it():
    today = (NOW.replace(hour=0, minute=0, second=0, microsecond=0) - datetime(2026, 1, 1, tzinfo=UTC)).days
    with pytest.raises(BitstampError):
        parse_ohlc(envelope([candle_row(today)]), now=NOW)
    parse_ohlc(envelope([candle_row(today - 1)]), now=NOW)  # yesterday is complete


# ---------------- pagination ----------------
def paged_handler(pages, seen):
    def handler(request):
        params = dict(request.url.params)
        seen.append(params)
        index = len(seen) - 1
        return httpx.Response(200, json=envelope(pages[index] if index < len(pages) else []))
    return handler


def at_day(n, hour=6):  # a "now" whose newest completed UTC day is day n - 1
    return day(n).replace(hour=hour)


def test_forward_pagination_uses_start_and_limit_only_and_advances_from_the_newest_timestamp(monkeypatch):
    monkeypatch.setattr(bitstamp_btc, "OHLC_PAGE_LIMIT", 3)
    seen, sleeps = [], []
    pages = [[candle_row(0), candle_row(1), candle_row(2)], [candle_row(3), candle_row(5), candle_row(6)], [candle_row(7)]]  # day 4 absent

    async def fake_sleep(seconds):
        sleeps.append(seconds)
    candles, n = run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler(pages, seen)), now=at_day(8), sleep=fake_sleep))
    assert n == 3 and len(seen) == 3
    assert [c.candle_open_at for c in candles] == [day(0), day(1), day(2), day(3), day(5), day(6), day(7)]  # day 4 stays missing
    assert all(set(p) == {"step", "limit", "exclude_current_candle", "start"} and "end" not in p for p in seen)
    assert all(p["step"] == "86400" and p["limit"] == "3" and p["exclude_current_candle"] == "true" for p in seen)
    assert [int(p["start"]) for p in seen] == [int(day(0).timestamp()), int(day(2).timestamp()) + 86400, int(day(6).timestamp()) + 86400]
    assert sleeps == [bitstamp_btc.PAGE_DELAY_SECONDS, bitstamp_btc.PAGE_DELAY_SECONDS]  # a pause before every page after the first


def test_a_short_page_does_not_end_the_run_while_completed_days_remain():
    """Observed live: limit=1000 returned 999 candles with more history available."""
    seen = []
    pages = [[candle_row(0), candle_row(1)], [candle_row(2), candle_row(3)]]  # limit is 1000, so both pages are short
    candles, n = run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler(pages, seen)), now=at_day(4), delay=0))
    assert n == 2 and [c.candle_open_at for c in candles] == [day(0), day(1), day(2), day(3)]


def test_the_run_ends_at_the_newest_completed_day_without_an_extra_request():
    seen = []
    candles, n = run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler([[candle_row(0)]], seen)), now=at_day(1), delay=0))
    assert n == 1 and len(candles) == 1 and len(seen) == 1
    candles, n = run(fetch_daily_candles(date(2026, 1, 5), client=client_for(paged_handler([], seen)), now=at_day(4), delay=0))
    assert candles == [] and n == 0  # start is already today: nothing completed to request


def test_an_empty_page_before_the_newest_completed_day_fails_closed():
    with pytest.raises(BitstampError):
        run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler([[]], [])), now=at_day(10), delay=0))
    with pytest.raises(BitstampError):  # a gap after the first page is not silently truncated
        run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler([[candle_row(0)], []], [])), now=at_day(10), delay=0))


def test_overlapping_non_advancing_or_early_pages_fail_closed(monkeypatch):
    with pytest.raises(BitstampError):  # page 2 repeats day 1
        run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler([[candle_row(0), candle_row(1)], [candle_row(1), candle_row(2)]], [])), now=at_day(4), delay=0))
    with pytest.raises(BitstampError):  # a page that starts before the requested start
        run(fetch_daily_candles(date(2026, 1, 5), client=client_for(paged_handler([[candle_row(0)]], [])), now=at_day(9), delay=0))
    monkeypatch.setattr(bitstamp_btc, "MAX_PAGES", 2)
    endless = [[candle_row(0)], [candle_row(1)], [candle_row(2)]]
    with pytest.raises(BitstampError):
        run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler(endless, [])), now=at_day(10), delay=0))


def test_no_interpolation_or_forward_fill_of_missing_or_zero_volume_days():
    rows = [candle_row(0), candle_row(2, o="50", h="50", l="50", c="50", v="0")]
    candles, _ = run(fetch_daily_candles(date(2026, 1, 1), client=client_for(paged_handler([rows], [])), now=at_day(3), delay=0))
    assert [c.candle_open_at for c in candles] == [day(0), day(2)]  # 2026-01-02 is not invented
    assert candles[1].volume_btc == 0 and candles[1].close == Decimal("50")  # kept exactly as published


def test_adapter_never_sends_end_or_stores_raw_payloads():
    source = inspect.getsource(bitstamp_btc)
    assert '"end"' not in source and "open(" not in source and "write_text" not in source and "write_bytes" not in source


# ---------------- provider policy ----------------
def test_bitstamp_policy_is_local_only_and_does_not_broaden_rights():
    p = BITSTAMP_BTC_POLICY
    assert (p.provider_id, p.dataset_id, p.name, p.source_url) == ("bitstamp", "bitstamp_btcusd_spot", "Bitstamp BTC/USD Spot", "https://www.bitstamp.net/api/")
    assert (p.authentication, p.normalized_storage_allowed, p.raw_response_storage) == ("none", True, "disabled")
    assert (p.redistribution, p.commercial_use) == ("review_required", "review_required")
    assert p.attribution_required is False and p.personal_data_expected is False and p.classification == "public_market_data"
    assert set(p.required_provenance) == {"source", "market", "source_url"}
    assert set(p.required_timestamps) == {"observed_at", "candle_open_at", "retrieved_at"}
    assert PROVIDER_POLICIES[p.dataset_id] is p
    assert "partners@bitstamp.net" in p.request_guidance and "review_required" in p.request_guidance and "all-markets" in p.request_guidance


# ---------------- BTC/ZMW derivation ----------------
def fx(mid="20.4451", observed=NOW - timedelta(hours=22)):
    return LatestFxMid(mid=Decimal(mid), observed_at=observed, retrieved_at=observed, source="boz", source_url="https://www.boz.zm/markets-securities/boz-exchange-rates")


def quote(last="67185.23", observed=NOW - timedelta(seconds=30)):
    return parse_ticker(ticker(last=last, timestamp=str(int(observed.timestamp()))), retrieved_at=NOW)


def fresh_quote():
    seen = datetime.now(UTC).replace(microsecond=0)
    return parse_ticker(ticker(timestamp=str(int(seen.timestamp()))), retrieved_at=seen + timedelta(seconds=5))


def test_exact_decimal_multiplication_without_rounding():
    q = derive_btc_zmw(quote("67185.23"), fx("20.4451"), now=NOW)
    exact = Decimal("67185.23") * Decimal("20.4451")
    assert q.btc_zmw == exact and isinstance(q.btc_zmw, Decimal)
    awkward = derive_btc_zmw(quote("0.00000001"), fx("0.00000001"), now=NOW)
    assert awkward.btc_zmw == Decimal("1E-16")  # no rounding to display precision
    big = derive_btc_zmw(quote("123456789.123456789"), fx("98765.4321987654"), now=NOW)
    with localcontext() as context:
        context.prec = 60
        assert big.btc_zmw == Decimal("123456789.123456789") * Decimal("98765.4321987654")  # 33 digits: exact at 40 digits


def test_components_provenance_and_classification_are_retained():
    q = derive_btc_zmw(quote(), fx(), now=NOW)
    assert (q.classification, q.derived, q.symbol, q.methodology_id, q.currency, q.unit) == ("derived", True, "BTCZMW", "btc_zmw_live_v1", "ZMW", "bitcoin")
    assert (q.btc_source, q.btc_market, q.btc_price_type, q.fx_source, q.fx_pair, q.fx_price) == ("bitstamp", "BTC/USD", "last_trade", "boz", "USDZMW", "mid")
    assert q.btc_usd_last == Decimal("67185.23") and q.usd_zmw_mid == Decimal("20.4451")
    assert q.btc_observed_at == NOW - timedelta(seconds=30) and q.fx_observed_at == NOW - timedelta(hours=22) and q.derived_at == NOW
    assert q.btc_source_url == BITSTAMP_TICKER_URL and q.fx_source_url.startswith("https://www.boz.zm/")
    assert q.notice == BTC_ZMW_NOTICE and "Not an executable BTC/ZMW market quote" in q.notice and "last trade" in q.methodology


def test_btc_current_and_fx_older_but_not_stale_keeps_the_age_visible():
    weekend_fx = fx(observed=NOW - timedelta(hours=66))
    q = derive_btc_zmw(quote(), weekend_fx, now=NOW)
    assert (q.btc_age_seconds, q.fx_age_seconds) == (30, 66 * 3600)
    assert (q.btc_stale, q.fx_stale, q.derived_stale) == (False, False, False)


def test_stale_states_follow_the_unchanged_rules_and_derived_is_their_or():
    assert derive_btc_zmw(quote(observed=NOW - timedelta(minutes=6)), fx(), now=NOW).derived_stale is True
    q = derive_btc_zmw(quote(observed=NOW - timedelta(minutes=6)), fx(), now=NOW)
    assert (q.btc_stale, q.fx_stale, q.derived_stale) == (True, False, True)
    assert derive_btc_zmw(quote(), fx(observed=NOW - timedelta(hours=96)), now=NOW).fx_stale is False  # exactly 96 h is not stale
    q = derive_btc_zmw(quote(), fx(observed=NOW - timedelta(hours=96, seconds=1)), now=NOW)
    assert (q.btc_stale, q.fx_stale, q.derived_stale) == (False, True, True)
    q = derive_btc_zmw(quote(observed=NOW - timedelta(minutes=9)), fx(observed=NOW - timedelta(hours=100)), now=NOW)
    assert (q.btc_stale, q.fx_stale, q.derived_stale) == (True, True, True)


def test_future_stored_fx_observation_is_rejected_by_the_boz_freshness_helper():
    with pytest.raises(ValueError):
        derive_btc_zmw(quote(), fx(observed=NOW + timedelta(hours=1)), now=NOW)


def test_boz_stale_policy_is_unchanged_and_reused():
    from app.services import boz
    assert boz.FX_STALE_AFTER == timedelta(hours=96)
    assert btc_zmw_module.fx_is_stale is boz.is_stale
    source = inspect.getsource(btc_zmw_module)
    for forbidden in ("httpx", "get_boz", "fetch_boz", "INSERT", "get_engine", "sqlalchemy"):
        assert forbidden not in source


# ---------------- API (offline) ----------------
client = TestClient(app)


@pytest.fixture
def api(monkeypatch):
    state = {"quote": None, "fx": fx(), "history": []}

    async def live():
        if isinstance(state["quote"], Exception):
            raise state["quote"]
        return state["quote"] or fresh_quote()

    def stored_fx():
        if isinstance(state["fx"], Exception):
            raise state["fx"]
        return state["fx"]
    monkeypatch.setattr(router_module, "get_bitstamp_btc_quote", live)
    monkeypatch.setattr(router_module, "fetch_latest_fx_mid", stored_fx)

    async def no_boz(*a, **k):
        raise AssertionError("BTC endpoints must never call BoZ")
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_boz)
    return state


def test_btc_usd_latest_success_and_json_decimal_strings(api):
    body = client.get("/api/v1/btc/usd/latest").json()
    assert body["last"] == "67185.23" and body["market"] == "BTC/USD" and body["market_type"] == "SPOT" and body["price_type"] == "last_trade"
    assert body["source"] == "bitstamp" and body["stale"] is False and body["volume_24h_btc"] == "1156.58547707"


def test_btc_usd_latest_maps_upstream_errors(api):
    api["quote"] = httpx.ReadTimeout("slow")
    assert client.get("/api/v1/btc/usd/latest").status_code == 504
    for exc in (httpx.ConnectError("down"), BitstampError("bad shape"), ValueError("bad json"),
                httpx.HTTPStatusError("503", request=httpx.Request("GET", "x"), response=httpx.Response(503))):
        api["quote"] = exc
        response = client.get("/api/v1/btc/usd/latest")
        assert response.status_code == 502 and "Bitstamp" in response.json()["detail"] and "Bank of Zambia" not in response.json()["detail"]


def test_btc_zmw_latest_success_uses_stored_fx_only(api):
    body = client.get("/api/v1/btc/zmw/latest").json()
    assert body["classification"] == "derived" and body["derived"] is True and body["symbol"] == "BTCZMW"
    assert Decimal(body["btc_zmw"]) == Decimal(body["btc_usd_last"]) * Decimal(body["usd_zmw_mid"])
    assert body["fx_source"] == "boz" and body["btc_source"] == "bitstamp" and body["fx_stale"] is False
    assert set(("btc_age_seconds", "fx_age_seconds", "btc_stale", "fx_stale", "derived_stale", "derived_at", "btc_observed_at", "fx_observed_at")) <= set(body)


def test_btc_zmw_error_mapping_is_distinct(api):
    api["quote"] = httpx.ReadTimeout("slow")
    assert client.get("/api/v1/btc/zmw/latest").status_code == 504
    api["quote"] = BitstampError("bad")
    r = client.get("/api/v1/btc/zmw/latest")
    assert r.status_code == 502 and "Bitstamp" in r.json()["detail"]
    api["quote"] = None
    api["fx"] = None
    r = client.get("/api/v1/btc/zmw/latest")
    assert r.status_code == 404 and "No stored Bank of Zambia" in r.json()["detail"]
    api["fx"] = RuntimeError("postgresql://u:SECRET@h/db")
    r = client.get("/api/v1/btc/zmw/latest")
    assert r.status_code == 503 and "SECRET" not in r.text and "Bitstamp" not in r.json()["detail"]
    api["fx"] = fx(observed=datetime.now(UTC) + timedelta(hours=3))
    assert client.get("/api/v1/btc/zmw/latest").status_code == 503


def test_btc_zmw_does_not_call_bitstamp_when_no_fx_is_stored(api):
    api["fx"] = None
    calls = []

    async def counting():
        calls.append(1)
        return fresh_quote()
    router_module.get_bitstamp_btc_quote = counting
    assert client.get("/api/v1/btc/zmw/latest").status_code == 404 and calls == []


def test_history_endpoint_reads_storage_only(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("history must never reach Bitstamp or BoZ")
    monkeypatch.setattr(router_module, "get_bitstamp_btc_quote", no_network)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_network)
    from app.schemas.btc import BtcDailyCandle
    seen = []
    candles = [BtcDailyCandle(candle_open_at=day(0), open=Decimal("1.00"), high=Decimal("2.00"), low=Decimal("0.50"), close=Decimal("1.50"), volume_btc=Decimal("0.00000000"))]
    monkeypatch.setattr(router_module, "fetch_btc_history", lambda r, now: seen.append(r) or candles)
    body = client.get("/api/v1/btc/usd/history?range=3Y").json()
    assert seen == ["3Y"] and body["range"] == "3Y" and body["count"] == 1 and body["market"] == "BTC/USD" and body["interval"] == "1d"
    assert body["observations"][0] == {"candle_open_at": "2026-01-01T00:00:00Z", "open": "1.00", "high": "2.00", "low": "0.50", "close": "1.50", "volume_btc": "0.00000000"}
    assert client.get("/api/v1/btc/usd/history").status_code == 200 and seen[-1] == "1Y"
    assert client.get("/api/v1/btc/usd/history?range=2W").status_code == 422


def test_history_database_failure_is_a_safe_503(monkeypatch):
    def boom(r, now):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_btc_history", boom)
    r = client.get("/api/v1/btc/usd/history?range=1M")
    assert r.status_code == 503 and "SECRET" not in r.text


# ---------------- backfill job (offline, no DB) ----------------
def test_backfill_dry_run_counts_without_touching_the_database(monkeypatch):
    from app.jobs import backfill_btc
    rows = [candle_row(0), candle_row(1, o="9", h="9", l="9", c="9", v="0"), candle_row(2)]
    monkeypatch.setattr(backfill_btc, "save_daily_candles", lambda *a, **k: pytest.fail("dry run must not write"))
    result = run(backfill_btc.run_import(date(2026, 1, 1), None, dry_run=True, client=client_for(paged_handler([rows], [])), now=at_day(3), delay=0))
    assert result == {"pages": 1, "candles": 3, "zero_volume": 1, "first": "2026-01-01", "last": "2026-01-03", "inserted": 0, "already_present": 0}


def test_backfill_to_filters_and_validates_arguments():
    from app.jobs import backfill_btc
    rows = [candle_row(0), candle_row(1), candle_row(2)]
    result = run(backfill_btc.run_import(date(2026, 1, 1), date(2026, 1, 2), dry_run=True, client=client_for(paged_handler([rows], [])), now=at_day(3), delay=0))
    assert result["candles"] == 2 and result["last"] == "2026-01-02"
    with pytest.raises(backfill_btc.BackfillError):
        run(backfill_btc.run_import(date(2026, 1, 5), date(2026, 1, 1), dry_run=True, now=NOW))
    for bad in ("2026-1-1", "20260101", "2026-02-30", "x"):
        with pytest.raises(Exception):
            backfill_btc.parse_date(bad)
    assert backfill_btc.DEFAULT_START == date(2011, 8, 17)


def test_backfill_main_reports_failures_without_leaking(monkeypatch, capsys):
    from app.jobs import backfill_btc

    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(backfill_btc, "run_import", boom)
    assert backfill_btc.main(["--dry-run"]) == 1
    assert "SECRET" not in capsys.readouterr().err


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
        c.execute(text((init / "010_btc_usd_daily.sql").read_text()))
    yield eng
    eng.dispose()


def dc(n, o="100.00", h="110.00", l="95.00", c="105.00", v="12.50000000"):
    return DailyCandle(day(n), Decimal(o), Decimal(h), Decimal(l), Decimal(c), Decimal(v))


def test_pg_insert_identical_noop_and_conflict_rolls_back(engine):
    assert save_daily_candles([dc(0), dc(1)], retrieved_at=NOW, engine=engine) == (2, 0)
    assert save_daily_candles([dc(0, o="100.0", v="12.5")], retrieved_at=NOW, engine=engine) == (0, 1)  # numerically equal
    with pytest.raises(BtcConflictError) as info:
        save_daily_candles([dc(2), dc(1, c="106.00")], retrieved_at=NOW, engine=engine)
    assert "2026-01-02" in str(info.value) and "106" not in str(info.value)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM btc_usd_daily")).scalar() == 2  # dc(2) was rolled back
        assert c.execute(text("SELECT close FROM btc_usd_daily WHERE candle_open_at = :t"), {"t": day(1)}).scalar() == Decimal("105.00")


def test_pg_history_ranges_ordering_and_value_preservation(engine):
    saved = [dc(0), dc(1, v="0.00000000", o="8.88", h="8.88", l="8.88", c="8.88"), dc(40), dc(41, o="100.123456789", h="110", l="95", c="105.5", v="0.12345678")]
    save_daily_candles(list(reversed(saved)), retrieved_at=NOW, engine=engine)  # input order does not matter
    now = day(45).replace(hour=15)
    allc = fetch_btc_history("ALL", now=now, engine=engine)
    assert [c.candle_open_at for c in allc] == [day(0), day(1), day(40), day(41)]  # ascending, day 2..39 absent
    assert allc[1].volume_btc == 0 and allc[1].open == allc[1].close == Decimal("8.88")  # zero-volume preserved
    assert allc[3].open == Decimal("100.123456789") and str(allc[3].volume_btc) == "0.12345678"
    assert [c.candle_open_at for c in fetch_btc_history("1M", now=now, engine=engine)] == [day(40), day(41)]  # since = day 15 00:00
    assert len(fetch_btc_history("3M", now=now, engine=engine)) == 4
    assert range_start("ALL", now) is None and range_start("1M", now) == day(15)


def test_pg_constraints_reject_invalid_rows_and_non_midnight_candles(engine):
    bad = {"low": "99.00", "high": "98.00"}
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO btc_usd_daily (provider, market, interval, candle_open_at, open, high, low, close, volume_btc, source_url, retrieved_at) "
                           "VALUES ('bitstamp','BTC/USD','1d', :t, 100, :high, :low, 100, 1, 'u', now())"), {"t": day(0), **bad})
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO btc_usd_daily (provider, market, interval, candle_open_at, open, high, low, close, volume_btc, source_url, retrieved_at) "
                           "VALUES ('bitstamp','BTC/USD','1d', :t, 100, 110, 90, 100, 1, 'u', now())"), {"t": day(0) + timedelta(hours=3)})


def test_pg_import_end_to_end_with_mocked_provider_stores_no_raw_payload(engine):
    from app.jobs import backfill_btc
    rows = [candle_row(0), candle_row(1, o="9", h="9", l="9", c="9", v="0"), candle_row(3)]
    result = run(backfill_btc.run_import(date(2026, 1, 1), None, client=client_for(paged_handler([rows], [])), engine=engine, now=at_day(4), delay=0))
    assert (result["inserted"], result["already_present"], result["zero_volume"]) == (3, 0, 1)
    again = run(backfill_btc.run_import(date(2026, 1, 1), None, client=client_for(paged_handler([rows], [])), engine=engine, now=at_day(4), delay=0))
    assert (again["inserted"], again["already_present"]) == (0, 3)
    with engine.connect() as c:
        cols = [r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'btc_usd_daily'"))]
        assert not [x for x in cols if "raw" in x or "payload" in x or "json" in x]
        assert c.execute(text("SELECT count(*) FROM btc_usd_daily WHERE volume_btc = 0")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM btc_usd_daily WHERE candle_open_at = :t"), {"t": day(2)}).scalar() == 0  # no fill
