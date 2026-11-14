"""PostgreSQL persistence for completed daily BTC/USD candles (normalized values only; no raw payloads, no live ticks)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.btc import BITSTAMP_OHLC_URL, BtcDailyCandle
from app.services.bitstamp_btc import DailyCandle

# Rolling-day windows counted back from the request time (not calendar months or years): 1M = 30 days, 3M = 91, 6M = 182,
# 1Y = 365, 3Y = 1095, 5Y = 1825, ALL = unbounded. The cut-off is the UTC midnight of the first day inside the window.
RANGE_WINDOWS: dict[str, timedelta | None] = {
    "1M": timedelta(days=30), "3M": timedelta(days=91), "6M": timedelta(days=182),
    "1Y": timedelta(days=365), "3Y": timedelta(days=3 * 365), "5Y": timedelta(days=5 * 365), "ALL": None,
}
MAX_CONFLICTS_SHOWN = 5

_INSERT = text(
    """
    INSERT INTO btc_usd_daily
        (provider, market, interval, candle_open_at, open, high, low, close, volume_btc, source_url, retrieved_at)
    VALUES ('bitstamp', 'BTC/USD', '1d', :candle_open_at, :open, :high, :low, :close, :volume_btc, :source_url, :retrieved_at)
    ON CONFLICT (provider, market, interval, candle_open_at) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT open, high, low, close, volume_btc FROM btc_usd_daily
    WHERE provider = 'bitstamp' AND market = 'BTC/USD' AND interval = '1d' AND candle_open_at = :candle_open_at
    """
)


class BtcConflictError(Exception):
    """A stored candle differs from the incoming one. Names candle dates only, never a price."""

    def __init__(self, count: int, dates: list[str] | None = None):
        shown = (dates or [])[:MAX_CONFLICTS_SHOWN]
        detail = (": " + ", ".join(shown)) if shown else ""
        if dates and len(dates) > len(shown):
            detail += f" ... and {len(dates) - len(shown)} more"
        super().__init__(f"{count} stored BTC/USD daily candle(s) conflict with the published value{detail}; nothing was written")
        self.count = count


def save_daily_candles(
    candles: list[DailyCandle], *, retrieved_at: datetime, source_url: str = BITSTAMP_OHLC_URL, engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert candles; returns (inserted, already_present_identical). Never overwrites; any conflict rolls back all."""
    inserted = identical = 0
    conflicts: list[str] = []
    with (engine or get_engine()).begin() as connection:
        for candle in candles:
            params = {"candle_open_at": candle.candle_open_at, "open": candle.open, "high": candle.high, "low": candle.low,
                      "close": candle.close, "volume_btc": candle.volume_btc, "source_url": source_url, "retrieved_at": retrieved_at}
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            elif tuple(connection.execute(_SELECT_ONE, params).one()) == (candle.open, candle.high, candle.low, candle.close, candle.volume_btc):
                identical += 1  # numeric comparison: 8.0 == 8.00
            else:
                conflicts.append(f"{candle.candle_open_at:%Y-%m-%d}")
        if conflicts:  # raising inside begin() rolls the whole batch back
            raise BtcConflictError(len(conflicts), sorted(conflicts))
    return inserted, identical


_HISTORY = text(
    """
    SELECT candle_open_at, open, high, low, close, volume_btc FROM btc_usd_daily
    WHERE provider = 'bitstamp' AND market = 'BTC/USD' AND interval = '1d'
      AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR candle_open_at >= :since)
    ORDER BY candle_open_at ASC
    """
)


def range_start(range_: str, now: datetime) -> datetime | None:
    """UTC midnight of the first day inside the window, so a whole candle is never cut."""
    window = RANGE_WINDOWS[range_]
    if window is None:
        return None
    return (now.astimezone(timezone.utc) - window).replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_btc_history(range_: str, *, now: datetime | None = None, engine: Engine | None = None) -> list[BtcDailyCandle]:
    """Stored candles only, ascending. Days Bitstamp did not publish stay absent."""
    since = range_start(range_, now or datetime.now(timezone.utc))
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"since": since}).mappings().all()
    return [BtcDailyCandle(**row) for row in rows]
