"""PostgreSQL persistence for normalized ERB fuel prices (no raw workbooks)."""

from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.fuel import FUEL_PRODUCTS, FuelHistoryPoint, FuelPrice
from app.services.erb_fuel import FuelRow

_INSERT = text(
    """
    INSERT INTO zambia_fuel_prices
        (effective_date, product, price_type, price_zmw, unit, source, source_url, retrieved_at)
    VALUES (:effective_date, :product, :price_type, :price_zmw, :unit, 'erb', :source_url, :retrieved_at)
    ON CONFLICT (effective_date, product, price_type, unit) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT price_zmw FROM zambia_fuel_prices
    WHERE effective_date = :effective_date AND product = :product AND price_type = :price_type AND unit = :unit
    """
)


class FuelConflictError(Exception):
    def __init__(self, count: int):
        super().__init__(f"{count} stored price(s) conflict with the ERB archive; nothing was written")
        self.count = count


def save_fuel_prices(
    rows: list[FuelRow], *, retrieved_at: datetime, source_url: str, engine: Engine | None = None
) -> tuple[int, int]:
    """Atomically insert; returns (inserted, already_present). Never overwrites; any conflict rolls back all."""
    inserted = identical = conflicts = 0
    with (engine or get_engine()).begin() as connection:
        for row in rows:
            params = {"effective_date": row.effective_date, "product": row.product, "price_type": row.price_type,
                      "price_zmw": row.price_zmw, "unit": row.unit, "source_url": source_url,
                      "retrieved_at": retrieved_at}
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            elif connection.execute(_SELECT_ONE, params).scalar_one() == row.price_zmw:  # numeric compare
                identical += 1
            else:
                conflicts += 1
        if conflicts:
            raise FuelConflictError(conflicts)
    return inserted, identical


_LATEST = text(
    """
    SELECT DISTINCT ON (product) product, effective_date, price_zmw, unit, price_type, source, source_url, retrieved_at
    FROM zambia_fuel_prices WHERE price_type = 'retail'
    ORDER BY product, effective_date DESC
    """
)


def fetch_latest_fuel(engine: Engine | None = None) -> list[FuelPrice]:
    with (engine or get_engine()).connect() as connection:
        found = {r["product"]: r for r in connection.execute(_LATEST).mappings().all()}
    return [FuelPrice(**found[p]) for p in FUEL_PRODUCTS if p in found]


RANGE_DAYS: dict[str, int | None] = {"1Y": 365, "3Y": 1095, "5Y": 1826, "ALL": None}
_HISTORY = text(
    """
    SELECT product, effective_date, price_zmw, unit, price_type, source FROM zambia_fuel_prices
    WHERE product = :product AND price_type = :price_type
      AND (CAST(:since AS DATE) IS NULL OR effective_date >= :since)
    ORDER BY effective_date ASC
    """
)


def fetch_fuel_history(
    product: str, price_type: str, range_: str, *, today: date, engine: Engine | None = None
) -> list[FuelHistoryPoint]:
    """Stored observations only, ascending, never interpolated or filled."""
    days = RANGE_DAYS[range_]
    since = None if days is None else today - timedelta(days=days)
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"product": product, "price_type": price_type, "since": since}).mappings().all()
    return [FuelHistoryPoint(**r) for r in rows]
