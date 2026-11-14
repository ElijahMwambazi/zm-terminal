"""PostgreSQL persistence for normalized USD/ZMW observations (no raw payloads)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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


def fetch_fx_history(
    range_: str, *, now: datetime | None = None, engine: Engine | None = None
) -> list[FxHistoryPoint]:
    since = range_start(range_, now or datetime.now(timezone.utc))
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_SELECT, {"since": since}).mappings().all()
    return [FxHistoryPoint(**row) for row in rows]


class FxConflictError(Exception):
    """A stored observation has different values than the incoming one for the same key."""


_SELECT_ONE = text(
    """
    SELECT buy, mid, sell FROM fx_rates
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW'
      AND observed_at = :observed_at AND source = :source
    """
)


def save_fx_observations(
    observations: dict[datetime, tuple[Decimal, Decimal, Decimal]],
    *, retrieved_at: datetime, source: str, source_url: str, engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert observations; returns (inserted, already_present_identical).

    Existing rows are never overwritten. If any existing row has different values
    (numeric comparison, so 19.8 == 19.8000) the whole batch is rolled back.
    """
    inserted = identical = 0
    conflicts: list[datetime] = []
    with (engine or get_engine()).begin() as connection:
        for observed_at, (buy, mid, sell) in sorted(observations.items()):
            params = {
                "observed_at": observed_at, "buy": buy, "mid": mid, "sell": sell,
                "source": source, "retrieved_at": retrieved_at, "source_url": source_url,
            }
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
                continue
            existing = connection.execute(_SELECT_ONE, params).one()
            if tuple(existing) == (buy, mid, sell):
                identical += 1
            else:
                conflicts.append(observed_at)
        if conflicts:  # raising inside begin() rolls the whole batch back
            stamps = ", ".join(c.isoformat() for c in conflicts[:5])
            raise FxConflictError(f"{len(conflicts)} stored observation(s) conflict with BoZ values, e.g. {stamps}")
    return inserted, identical


def save_fx_quote(quote: FxQuote, *, engine: Engine | None = None) -> bool:
    """Conflict-aware single save; returns True if a row was added, False if already present.

    Shares save_fx_observations' rules: never overwrites, identical values (numeric
    comparison) are a silent no-op, different values raise FxConflictError.
    """
    inserted, _ = save_fx_observations(
        {quote.observed_at: (quote.buy, quote.mid, quote.sell)},
        retrieved_at=quote.retrieved_at, source=quote.source, source_url=quote.source_url, engine=engine,
    )
    return inserted == 1


@dataclass(frozen=True)
class LatestFxMid:
    mid: Decimal
    observed_at: datetime
    retrieved_at: datetime
    source: str
    source_url: str


_LATEST_MID = text(
    """
    SELECT mid, observed_at, retrieved_at, source, source_url FROM fx_rates
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW' AND source = 'boz'
      AND buy IS NOT NULL AND mid IS NOT NULL AND sell IS NOT NULL
    ORDER BY observed_at DESC LIMIT 1
    """
)


def fetch_latest_fx_mid(*, engine: Engine | None = None) -> LatestFxMid | None:
    """Newest complete stored BoZ USD/ZMW observation (read-only; never contacts BoZ). None when nothing is stored."""
    with (engine or get_engine()).connect() as connection:
        row = connection.execute(_LATEST_MID).mappings().first()
    return None if row is None else LatestFxMid(**row)
