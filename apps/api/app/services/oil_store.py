"""PostgreSQL persistence for monthly global oil benchmark prices (normalized values only, no raw workbooks)."""

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.oil import (
    OIL_BENCHMARKS, OIL_PROVIDER, OIL_SERIES, OIL_STALE_AFTER_MONTHS, OilObservation, OilPoint,
)

_INSERT = text(
    """
    INSERT INTO global_oil_prices
        (benchmark, provider, period_start, period_end, value, currency, unit, publication_date,
         source_series, source_url, retrieved_at)
    VALUES (:benchmark, 'world_bank', :period_start, :period_end, :value, 'USD', 'barrel', :publication_date,
            :source_series, :source_url, :retrieved_at)
    ON CONFLICT (provider, benchmark, period_start, period_end) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT value FROM global_oil_prices
    WHERE provider = 'world_bank' AND benchmark = :benchmark AND period_start = :period_start AND period_end = :period_end
    """
)


class OilConflictError(Exception):
    def __init__(self, count: int):
        super().__init__(f"{count} stored oil month(s) conflict with the published value; nothing was written")
        self.count = count


def save_oil_observations(
    points: list[OilPoint], *, publication_date: date, source_url: str, retrieved_at: datetime,
    engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert one release; returns (inserted, already_present). Never overwrites; any conflict rolls back all.

    Same identity and numerically equal value is a no-op; a different value is a conflict (no silent revision).
    """
    inserted = identical = conflicts = 0
    with (engine or get_engine()).begin() as connection:
        for point in points:
            params = {"benchmark": point.benchmark, "period_start": point.period_start, "period_end": point.period_end,
                      "value": point.value, "publication_date": publication_date,
                      "source_series": OIL_SERIES[point.benchmark].source_series, "source_url": source_url,
                      "retrieved_at": retrieved_at}
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            elif connection.execute(_SELECT_ONE, params).scalar_one() == point.value:  # numeric compare
                identical += 1
            else:
                conflicts += 1
        if conflicts:
            raise OilConflictError(conflicts)
    return inserted, identical


def months_behind(period_start: date, today: date) -> int:
    """Whole calendar months from a stored month to the current month (previous month = 1)."""
    return (today.year - period_start.year) * 12 + today.month - period_start.month


def is_stale(period_start: date, *, today: date) -> bool:
    return months_behind(period_start, today) > OIL_STALE_AFTER_MONTHS


_LATEST = text(
    """
    SELECT DISTINCT ON (benchmark) benchmark, period_start, period_end, value, currency, unit, publication_date,
           source_series, source_url, retrieved_at
    FROM global_oil_prices WHERE provider = 'world_bank'
    ORDER BY benchmark, period_start DESC
    """
)


def fetch_latest_oil(*, today: date, engine: Engine | None = None) -> list[OilObservation]:
    """Newest stored month per benchmark, in BRENT, WTI order; benchmarks with no rows are omitted."""
    with (engine or get_engine()).connect() as connection:
        found = {r["benchmark"]: r for r in connection.execute(_LATEST).mappings().all()}
    return [
        OilObservation(**found[b], name=OIL_SERIES[b].name, description=OIL_SERIES[b].description,
                       stale=is_stale(found[b]["period_start"], today=today))
        for b in OIL_BENCHMARKS if b in found
    ]


RANGE_YEARS: dict[str, int | None] = {"1Y": 1, "3Y": 3, "5Y": 5, "ALL": None}
_HISTORY = text(
    """
    SELECT benchmark, period_start, period_end, value, currency, unit FROM global_oil_prices
    WHERE provider = 'world_bank' AND benchmark = :benchmark
      AND (CAST(:since AS DATE) IS NULL OR period_start >= :since)
    ORDER BY period_start ASC
    """
)


def range_start(range_: str, today: date) -> date | None:
    """First day of the current month N years back, so 1Y is the latest 12 published months once the current month is out."""
    years = RANGE_YEARS[range_]
    return None if years is None else date(today.year - years, today.month, 1)


def fetch_oil_history(benchmark: str, range_: str, *, today: date, engine: Engine | None = None) -> list[OilPoint]:
    """Stored months only, ascending; missing months stay missing."""
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"benchmark": benchmark, "since": range_start(range_, today)}).mappings().all()
    return [OilPoint(**r) for r in rows]
