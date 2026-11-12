"""PostgreSQL persistence for normalized USD/ZMW observations (no raw payloads)."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.fx import FxHistoryPoint, FxQuote

HistoryRange = Literal["1D", "1W", "1M", "3M", "1Y", "ALL"]
RANGE_WINDOWS: dict[str, timedelta | None] = {
    "1D": timedelta(days=1),
    "1W": timedelta(days=7),
    "1M": timedelta(days=30),
    "3M": timedelta(days=90),
    "1Y": timedelta(days=365),
    "ALL": None,
}

_INSERT = text(
    """
    INSERT INTO fx_rates
        (base_currency, quote_currency, observed_at, buy, mid, sell, source, retrieved_at, source_url)
    VALUES ('USD', 'ZMW', :observed_at, :buy, :mid, :sell, :source, :retrieved_at, :source_url)
    ON CONFLICT (base_currency, quote_currency, observed_at, source) DO NOTHING
    """
)
_SELECT = text(
    """
    SELECT observed_at, buy, mid, sell, source FROM fx_rates
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW'
      AND buy IS NOT NULL AND mid IS NOT NULL AND sell IS NOT NULL
      AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR observed_at >= :since)
    ORDER BY observed_at ASC
    """
)


def range_start(range_: str, now: datetime) -> datetime | None:
    window = RANGE_WINDOWS[range_]
    return None if window is None else now.astimezone(timezone.utc) - window


def save_fx_quote(quote: FxQuote, *, engine: Engine | None = None) -> bool:
    """Insert once per (pair, observed_at, source); returns True if a row was added."""
    with (engine or get_engine()).begin() as connection:
        result = connection.execute(_INSERT, {
            "observed_at": quote.observed_at, "buy": quote.buy, "mid": quote.mid,
            "sell": quote.sell, "source": quote.source,
            "retrieved_at": quote.retrieved_at, "source_url": quote.source_url,
        })
    return result.rowcount == 1


def fetch_fx_history(
    range_: str, *, now: datetime | None = None, engine: Engine | None = None
) -> list[FxHistoryPoint]:
    since = range_start(range_, now or datetime.now(timezone.utc))
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_SELECT, {"since": since}).mappings().all()
    return [FxHistoryPoint(**row) for row in rows]
