"""Bitstamp public API adapter for BTC/USD (market btcusd, SPOT): live ticker and completed daily OHLCV candles.

Source integrity: an unknown market path can return HTTP 200 with the all-markets ARRAY, so HTTP 200 is never enough.
The single-market ticker body carries no pair identifier; the market identity rests on the configured `/ticker/btcusd/`
path, a strict response shape (an object, never an array) and `market_type == "SPOT"`. Every numeric field consumed is
validated as a finite decimal string and handled as Decimal (never float). Unauthenticated; no raw payload is stored.

Daily history is paged forward with `start` + `limit` only. If both `start` and `end` are sent Bitstamp uses `end`, so
`end` is never used. Zero-volume candles are valid published candles (observed source behaviour; generation
methodology not documented): they are accepted as published, never dropped, filled or inferred.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.schemas.btc import BITSTAMP_OHLC_URL, BITSTAMP_TICKER_URL, BTC_STALE_AFTER_SECONDS, BtcUsdQuote

BITSTAMP_TIMEOUT_SECONDS = 10.0
BITSTAMP_USER_AGENT = "ZM-Terminal/0.1 (Bitstamp BTCUSD adapter)"
BTC_STALE_AFTER = timedelta(seconds=BTC_STALE_AFTER_SECONDS)
FUTURE_TOLERANCE = timedelta(seconds=60)  # clock-skew allowance; a later source timestamp is rejected, not accepted
DAY_SECONDS = 86400
OHLC_PAGE_LIMIT = 1000  # Bitstamp's documented maximum
PAGE_DELAY_SECONDS = 1.0
MAX_PAGES = 50  # loop guard: 50,000 daily candles is about 137 years
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_UNIX_SECONDS = re.compile(r"[0-9]{1,12}")
_TICKER_FIELDS = ("last", "bid", "ask", "high", "low", "vwap", "volume")


class BitstampError(ValueError):
    """The Bitstamp response is not in the verified shape or contains an unusable value."""


def _decimal(value: object, name: str, *, allow_zero: bool) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise BitstampError(f"Invalid decimal for {name}")
    number = Decimal(value)
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise BitstampError(f"Out-of-range value for {name}")
    return number


def _unix(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not _UNIX_SECONDS.fullmatch(value):
        raise BitstampError(f"Invalid timestamp for {name}")
    return datetime.fromtimestamp(int(value), timezone.utc)


def is_btc_stale(observed_at: datetime, now: datetime) -> bool:
    """Older than 5 minutes is stale (a ZM Terminal heuristic); a clearly future observation fails."""
    for value in (observed_at, now):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Freshness requires timezone-aware timestamps")
    age = now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age < -FUTURE_TOLERANCE:
        raise BitstampError("Observation is in the future")
    return age > BTC_STALE_AFTER


def parse_ticker(payload: object, *, retrieved_at: datetime) -> BtcUsdQuote:
    if isinstance(payload, list):
        raise BitstampError("Expected a single-market ticker object, got an array (unknown market path?)")
    if not isinstance(payload, dict):
        raise BitstampError("Expected a ticker object")
    for key in ("last", "timestamp", "market_type"):
        if key not in payload:
            raise BitstampError(f"Missing ticker field {key}")
    if payload["market_type"] != "SPOT":
        raise BitstampError("Ticker is not a SPOT market")
    values = {name: _decimal(payload.get(name), name, allow_zero=name == "volume") for name in _TICKER_FIELDS}
    observed_at = _unix(payload["timestamp"], "timestamp")
    return BtcUsdQuote(
        last=values["last"], bid=values["bid"], ask=values["ask"], high_24h=values["high"], low_24h=values["low"],
        vwap_24h=values["vwap"], volume_24h_btc=values["volume"], observed_at=observed_at, retrieved_at=retrieved_at,
        stale=is_btc_stale(observed_at, retrieved_at),
    )


@dataclass(frozen=True)
class DailyCandle:
    candle_open_at: datetime  # UTC midnight
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_btc: Decimal


def parse_candle(row: object) -> DailyCandle:
    if not isinstance(row, dict):
        raise BitstampError("Expected a candle object")
    opened = _unix(row.get("timestamp"), "candle timestamp")
    if int(opened.timestamp()) % DAY_SECONDS != 0:
        raise BitstampError("Daily candle is not on a UTC midnight boundary")
    o, h, l, c = (_decimal(row.get(k), k, allow_zero=False) for k in ("open", "high", "low", "close"))
    volume = _decimal(row.get("volume"), "volume", allow_zero=True)  # zero volume is a valid published candle
    if not (l <= o <= h and l <= c <= h):
        raise BitstampError("Candle violates low <= open/close <= high")
    return DailyCandle(opened, o, h, l, c, volume)


def parse_ohlc(payload: object, *, now: datetime) -> list[DailyCandle]:
    """Validate one OHLC page: envelope, every candle, strictly ascending, and only completed UTC days."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise BitstampError("Unexpected OHLC response envelope")
    data = payload["data"]
    if "pair" in data and data["pair"] != "BTC/USD":
        raise BitstampError("OHLC response is for a different pair")
    if not isinstance(data.get("ohlc"), list):
        raise BitstampError("OHLC response has no candle array")
    candles = [parse_candle(row) for row in data["ohlc"]]
    if any(b.candle_open_at <= a.candle_open_at for a, b in zip(candles, candles[1:])):
        raise BitstampError("Candles are not strictly ascending (duplicate or out of order)")
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if candles and candles[-1].candle_open_at >= today:
        raise BitstampError("A current (open) candle was returned although it was excluded")
    return candles


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, str] | None = None) -> object:
    response = await client.get(
        url, params=params, timeout=BITSTAMP_TIMEOUT_SECONDS,
        headers={"User-Agent": BITSTAMP_USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise BitstampError("Expected a JSON response")
    return response.json()


async def get_bitstamp_btc_quote(*, client: httpx.AsyncClient | None = None) -> BtcUsdQuote:
    """One ticker request, no retry, nothing persisted. The optional client is a transport seam for offline tests."""
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await get_bitstamp_btc_quote(client=owned)
    payload = await _get_json(client, BITSTAMP_TICKER_URL)
    return parse_ticker(payload, retrieved_at=datetime.now(timezone.utc))


async def fetch_daily_candles(
    start: date, *, client: httpx.AsyncClient, delay: float = PAGE_DELAY_SECONDS, now: datetime | None = None,
    sleep: Callable[[float], object] = asyncio.sleep,
) -> tuple[list[DailyCandle], int]:
    """All completed daily candles from `start` (UTC date) forward; returns (candles, pages requested).

    Forward pagination with start + limit only (never `end`), `exclude_current_candle=true`, the next page starting at the
    day after the newest candle returned. A page can hold fewer than `limit` candles while more history exists (observed:
    999 for limit=1000), so a short page does not end the run: it ends when the newest completed UTC day has been reached.
    Enforced here: ascending, non-duplicate timestamps within a page; every page starts at or after the requested start and
    does not overlap the previous page (so each non-empty page advances at least one day); at most MAX_PAGES pages; and an
    empty page before the newest completed day fails closed (nothing is assumed, filled or truncated silently). Not
    enforced: that every interior day exists (the importer does not prove daily continuity; a gap shorter than a page is
    accepted as published). Edge: shortly after UTC midnight Bitstamp may not yet expose yesterday's completed candle; the
    importer then fails rather than declaring history complete, and a later run succeeds.
    """
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    latest_complete = int((today - timedelta(days=1)).timestamp())
    candles: list[DailyCandle] = []
    next_start = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    pages = 0
    while next_start <= latest_complete:
        if pages >= MAX_PAGES:
            raise BitstampError("Too many OHLC pages; stopping")
        if pages:
            await sleep(delay)
        payload = await _get_json(client, BITSTAMP_OHLC_URL, {
            "step": str(DAY_SECONDS), "limit": str(OHLC_PAGE_LIMIT), "exclude_current_candle": "true", "start": str(next_start)})
        pages += 1
        page = parse_ohlc(payload, now=now)
        if not page:
            raise BitstampError("No candles returned before the newest completed day (start before the first candle, or a gap)")
        if page[0].candle_open_at.timestamp() < next_start:
            raise BitstampError("Page starts before the requested start")
        if candles and page[0].candle_open_at <= candles[-1].candle_open_at:
            raise BitstampError("Page overlaps the previous page; refusing to continue")
        candles.extend(page)
        next_start = int(page[-1].candle_open_at.timestamp()) + DAY_SECONDS
    return candles, pages
