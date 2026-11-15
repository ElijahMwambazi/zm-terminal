"""PostgreSQL persistence for official BoZ monthly USD/ZMW averages (normalized values and provenance only; no raw PDFs)."""

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.fx_monthly import FX_MONTHLY_RANGE_MONTHS, FxMonthlyHistory, FxMonthlyPoint
from app.services.boz_monthly_table import (
    CURRENCY_REGIME, PUBLISHED_PRECISION, SOURCE, SOURCE_SERIES, SOURCE_TABLE,
)
from app.services.fx_monthly_combine import MonthlyConflictError, MonthlyObservation

_INSERT = text(
    """
    INSERT INTO fx_monthly_averages
        (base_currency, quote_currency, observation_month, buy, mid, sell, source, source_series, source_publication,
         source_url, source_page, source_table, published_precision, currency_regime, imported_at)
    VALUES ('USD', 'ZMW', :month, :buy, :mid, :sell, :source, :source_series, :publication, :source_url, :page, :table,
            :precision, :regime, :imported_at)
    ON CONFLICT (base_currency, quote_currency, observation_month, source_series) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT buy, mid, sell FROM fx_monthly_averages
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW' AND observation_month = :month AND source_series = :source_series
    """
)


def save_monthly_averages(
    observations: list[MonthlyObservation], *, imported_at: datetime, engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert; returns (inserted, already_present). Never overwrites.

    The same month with numerically equal buy/mid/sell is a no-op; a different value is a conflict and the whole import
    rolls back (no silent revision).
    """
    inserted = identical = 0
    conflicts: list[date] = []
    with (engine or get_engine()).begin() as connection:
        for o in observations:
            params = {
                "month": o.month, "buy": o.buy, "mid": o.mid, "sell": o.sell, "source": SOURCE, "source_series": SOURCE_SERIES,
                "publication": o.publication_title, "source_url": o.source_url, "page": o.source_page, "table": SOURCE_TABLE,
                "precision": PUBLISHED_PRECISION, "regime": CURRENCY_REGIME, "imported_at": imported_at,
            }
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            elif tuple(connection.execute(_SELECT_ONE, params).one()) == (o.buy, o.mid, o.sell):
                identical += 1
            else:
                conflicts.append(o.month)
        if conflicts:  # raising inside begin() rolls the whole import back
            raise MonthlyConflictError(len(conflicts), [f"{m:%Y-%m} (stored)" for m in sorted(conflicts)])
    return inserted, identical


_HISTORY = text(
    """
    SELECT observation_month, buy, mid, sell, source, source_series, source_publication, source_url, source_page,
           published_precision
    FROM fx_monthly_averages
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW' AND source_series = :source_series
    ORDER BY observation_month DESC
    LIMIT :limit
    """
)


def fetch_fx_monthly(range_: str, *, engine: Engine | None = None) -> FxMonthlyHistory:
    """The most recent N stored months (1Y=12, 3Y=36, 5Y=60, ALL=all), ascending. Stored rows only; nothing is filled."""
    limit = FX_MONTHLY_RANGE_MONTHS[range_]
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"source_series": SOURCE_SERIES, "limit": limit}).mappings().all()
    points = [
        FxMonthlyPoint(month=f"{row['observation_month']:%Y-%m}", **{k: v for k, v in row.items() if k != "observation_month"})
        for row in reversed(rows)
    ]
    return FxMonthlyHistory(
        range=range_, count=len(points), first_month=points[0].month if points else None,
        last_month=points[-1].month if points else None, observations=points,
    )
