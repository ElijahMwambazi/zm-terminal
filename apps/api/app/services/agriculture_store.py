"""PostgreSQL persistence for normalized Zambia agricultural price observations."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.agriculture import AgricultureHistoryPoint, AgriculturePrice
from app.services.moa_agriculture import AgricultureRow

_INSERT = text(
    """
    INSERT INTO zambia_agricultural_prices
      (commodity, market_type, provider, observed_date, period_start, period_end,
       publication_date, publication_date_source, season, grade, variety, location_scope,
       location, delivery_location, price_basis, source_price, source_currency, source_unit,
       normalized_zmw_per_kg, source, source_url, source_period_label, source_record_id, retrieved_at)
    VALUES
      (:commodity, :market_type, :provider, NULL, :period_start, :period_end,
       :publication_date, :publication_date_source, NULL, :grade, :variety, :location_scope,
       :location, NULL, :price_basis, :source_price, :source_currency, :source_unit,
       :normalized_zmw_per_kg, :source, :source_url, :source_period_label, NULL, :retrieved_at)
    ON CONFLICT DO NOTHING
    """
)
_SELECT_IDENTITY = text(
    """
    SELECT source_price, normalized_zmw_per_kg
    FROM zambia_agricultural_prices
    WHERE market_type = 'national_average'
      AND provider = :provider
      AND commodity = :commodity
      AND period_start = :period_start
      AND period_end = :period_end
      AND grade IS NOT DISTINCT FROM :grade
      AND variety IS NOT DISTINCT FROM :variety
      AND location_scope = :location_scope
      AND location IS NOT DISTINCT FROM :location
      AND source_unit = :source_unit
    """
)


class AgricultureConflictError(Exception):
    def __init__(self, count: int):
        super().__init__(f"{count} stored agriculture observation(s) conflict; nothing was written")
        self.count = count


def _params(row: AgricultureRow) -> dict:
    return row.__dict__.copy()


def save_agriculture_prices(rows: list[AgricultureRow], *, engine: Engine | None = None) -> tuple[int, int]:
    """Atomically insert; same identity + same price is a no-op, different price aborts the batch."""
    inserted = identical = conflicts = 0
    with (engine or get_engine()).begin() as connection:
        for row in rows:
            params = _params(row)
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
                continue
            existing = connection.execute(_SELECT_IDENTITY, params).mappings().one()
            if (
                existing["source_price"] == row.source_price
                and existing["normalized_zmw_per_kg"] == row.normalized_zmw_per_kg
            ):
                identical += 1
            else:
                conflicts += 1
        if conflicts:
            raise AgricultureConflictError(conflicts)
    return inserted, identical


_LATEST = text(
    """
    SELECT DISTINCT ON (commodity)
      commodity, market_type, provider, period_start, period_end, publication_date,
      publication_date_source, grade, variety, location_scope, location, price_basis,
      source_price, source_currency, source_unit, normalized_zmw_per_kg, source,
      source_url, source_period_label, retrieved_at
    FROM zambia_agricultural_prices
    WHERE market_type = 'national_average' AND provider = 'moa'
    ORDER BY commodity, period_start DESC
    """
)


def fetch_latest_agriculture(engine: Engine | None = None) -> list[AgriculturePrice]:
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_LATEST).mappings().all()
    return [AgriculturePrice(**row) for row in rows]


RANGE_DAYS: dict[str, int | None] = {"1Y": 365, "3Y": 1095, "ALL": None}
_HISTORY = text(
    """
    SELECT commodity, period_start, period_end, source_price, normalized_zmw_per_kg,
           source_unit, price_basis, source, source_url
    FROM zambia_agricultural_prices
    WHERE commodity = :commodity
      AND market_type = 'national_average'
      AND provider = 'moa'
      AND (CAST(:since AS DATE) IS NULL OR period_start >= :since)
    ORDER BY period_start ASC
    """
)


def fetch_agriculture_history(
    commodity: str, range_: str, *, today: date, engine: Engine | None = None,
) -> list[AgricultureHistoryPoint]:
    days = RANGE_DAYS[range_]
    since = None if days is None else today - timedelta(days=days)
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"commodity": commodity, "since": since}).mappings().all()
    return [AgricultureHistoryPoint(**row) for row in rows]
