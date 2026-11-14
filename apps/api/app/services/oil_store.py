"""PostgreSQL persistence for global oil benchmark observations (normalized values only, no raw payloads)."""

from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.oil import OIL_SERIES, OIL_SOURCE, OilPoint

_INSERT = text(
    """
    INSERT INTO global_oil_benchmarks
        (benchmark, observed_date, value, currency, unit, source, series_id, source_url, retrieved_at)
    VALUES (:benchmark, :observed_date, :value, 'USD', 'barrel', :source, :series_id, :source_url, :retrieved_at)
    ON CONFLICT (benchmark, observed_date, source) DO NOTHING
    """
)
_SELECT_ONE = text(
    "SELECT value FROM global_oil_benchmarks WHERE benchmark = :benchmark AND observed_date = :observed_date AND source = :source"
)


class OilConflictError(Exception):
    def __init__(self, count: int):
        super().__init__(f"{count} stored oil observation(s) conflict with the upstream value; nothing was written")
        self.count = count


def save_oil_observations(
    points: list[OilPoint], *, retrieved_at: datetime, engine: Engine | None = None
) -> tuple[int, int]:
    """Atomically insert; returns (inserted, already_present). Never overwrites; any conflict rolls back all.

    Same identity and numerically equal value is a no-op; a different value is a conflict (no silent revision).
    """
    inserted = identical = conflicts = 0
    with (engine or get_engine()).begin() as connection:
        for point in points:
            series = OIL_SERIES[point.benchmark]
            params = {"benchmark": point.benchmark, "observed_date": point.observed_date, "value": point.value,
                      "source": OIL_SOURCE, "series_id": series.series_id, "source_url": series.source_url,
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


RANGE_DAYS: dict[str, int | None] = {"1M": 31, "3M": 92, "1Y": 366, "5Y": 1827, "ALL": None}
_HISTORY = text(
    """
    SELECT benchmark, observed_date, value, currency, unit, source FROM global_oil_benchmarks
    WHERE benchmark = :benchmark AND (CAST(:since AS DATE) IS NULL OR observed_date >= :since)
    ORDER BY observed_date ASC
    """
)


def fetch_oil_history(benchmark: str, range_: str, *, today: date, engine: Engine | None = None) -> list[OilPoint]:
    """Stored observations only, ascending; gaps stay gaps."""
    days = RANGE_DAYS[range_]
    since = None if days is None else today - timedelta(days=days)
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"benchmark": benchmark, "since": since}).mappings().all()
    return [OilPoint(**r) for r in rows]
