"""PostgreSQL persistence for the monthly IMF Copper benchmark (normalized values only; no raw responses)."""

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.copper import (
    COPPER_SERIES, COPPER_SOURCE_DEFINITION, COPPER_STALE_AFTER_MONTHS, CopperObservation, CopperPoint,
)
from app.services.imf_pcps import CopperMonth
from app.services.oil_store import months_behind, range_start  # the same monthly range semantics as Oil (1Y, 3Y, 5Y, ALL)

MAX_CONFLICTS_SHOWN = 5

_INSERT = text(
    """
    INSERT INTO global_copper_prices
        (provider, benchmark, period_start, period_end, value, currency, unit, price_basis, source_series,
         source_definition, source_url, retrieved_at)
    VALUES ('imf', 'COPPER', :period_start, :period_end, :value, 'USD', 'metric_ton', 'monthly_average', :source_series,
            :source_definition, :source_url, :retrieved_at)
    ON CONFLICT (provider, benchmark, period_start, period_end) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT value FROM global_copper_prices
    WHERE provider = 'imf' AND benchmark = 'COPPER' AND period_start = :period_start AND period_end = :period_end
    """
)


class CopperConflictError(Exception):
    """A stored month differs from the published value. Names the benchmark and month only, never a price."""

    def __init__(self, count: int, identities: list[str] | None = None):
        shown = (identities or [])[:MAX_CONFLICTS_SHOWN]
        detail = (": " + ", ".join(shown)) if shown else ""
        if identities and len(identities) > len(shown):
            detail += f" ... and {len(identities) - len(shown)} more"
        super().__init__(f"{count} stored copper month(s) conflict with the published value{detail}; nothing was written")
        self.count = count
        self.identities = list(identities or [])


def month_end(first: date) -> date:
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def save_copper_months(
    months: list[CopperMonth], *, source_url: str, retrieved_at: datetime, engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert; returns (inserted, already_present). Never overwrites; any conflict rolls back the whole import.

    The same identity with a numerically equal value is a no-op (so 14507.970 equals 14507.97); a different value is a
    conflict (no silent revision).
    """
    inserted = identical = 0
    conflicts: list[date] = []
    with (engine or get_engine()).begin() as connection:
        for month in months:
            params = {"period_start": month.period_start, "period_end": month_end(month.period_start), "value": month.value,
                      "source_series": COPPER_SERIES, "source_definition": COPPER_SOURCE_DEFINITION,
                      "source_url": source_url, "retrieved_at": retrieved_at}
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            elif connection.execute(_SELECT_ONE, params).scalar_one() == month.value:  # numeric compare
                identical += 1
            else:
                conflicts.append(month.period_start)
        if conflicts:  # raising inside begin() rolls the whole import back
            raise CopperConflictError(len(conflicts), [f"COPPER {d:%Y-%m}" for d in sorted(conflicts)])
    return inserted, identical


def is_stale(period_start: date, *, today: date) -> bool:
    return months_behind(period_start, today) > COPPER_STALE_AFTER_MONTHS


_LATEST = text(
    """
    SELECT period_start, period_end, value, source_series, source_definition, source_url, retrieved_at
    FROM global_copper_prices WHERE provider = 'imf' AND benchmark = 'COPPER'
    ORDER BY period_start DESC LIMIT 1
    """
)


def fetch_latest_copper(*, today: date, engine: Engine | None = None) -> CopperObservation | None:
    """The newest stored month, or None when nothing is stored."""
    with (engine or get_engine()).connect() as connection:
        row = connection.execute(_LATEST).mappings().first()
    if row is None:
        return None
    return CopperObservation(**row, stale=is_stale(row["period_start"], today=today))


_HISTORY = text(
    """
    SELECT period_start, period_end, value FROM global_copper_prices
    WHERE provider = 'imf' AND benchmark = 'COPPER'
      AND (CAST(:since AS DATE) IS NULL OR period_start >= :since)
    ORDER BY period_start ASC
    """
)


def fetch_copper_history(range_: str, *, today: date, engine: Engine | None = None) -> list[CopperPoint]:
    """Stored months only, ascending; months the IMF did not publish stay absent."""
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"since": range_start(range_, today)}).mappings().all()
    return [CopperPoint(**row) for row in rows]
