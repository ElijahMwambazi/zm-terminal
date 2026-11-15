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

# Every stable canonical field participates in equality; retrieval/import time does not.
_STABLE_COLUMNS = (
    "base_currency", "quote_currency", "observation_month", "source", "source_series", "buy", "mid", "sell",
    "source_publication", "source_url", "source_page", "source_table", "published_precision", "currency_regime",
    "published_buy", "published_mid", "published_sell", "published_currency", "normalization_divisor",
)
_INSERT = text(
    "INSERT INTO fx_monthly_averages (" + ", ".join((*_STABLE_COLUMNS, "imported_at")) + ") VALUES ("
    + ", ".join(":" + field for field in (*_STABLE_COLUMNS, "imported_at")) + ") "
    "ON CONFLICT (base_currency, quote_currency, observation_month, source_series) DO NOTHING"
)
_SELECT_ONE = text(
    "SELECT " + ", ".join(_STABLE_COLUMNS) + " FROM fx_monthly_averages "
    "WHERE base_currency = :base_currency AND quote_currency = :quote_currency "
    "AND observation_month = :observation_month AND source_series = :source_series"
)


def row_params(o: MonthlyObservation, imported_at: datetime) -> dict:
    """All stored columns for one observation. A phase-1 observation (no published_* fields) is rebased ZMW: printed = canonical,
    divisor 1. Phase-2 rows carry their printed values, currency and divisor; the database re-checks canonical = printed / divisor."""
    phase1 = o.published_buy is None
    return {
        "base_currency": "USD", "quote_currency": "ZMW", "observation_month": o.month,
        "buy": o.buy, "mid": o.mid, "sell": o.sell, "source": SOURCE, "source_series": o.source_series or SOURCE_SERIES,
        "source_publication": o.publication_title, "source_url": o.source_url, "source_page": o.source_page,
        "source_table": o.source_table or SOURCE_TABLE,
        "published_precision": PUBLISHED_PRECISION if o.published_precision is None else o.published_precision,
        "currency_regime": CURRENCY_REGIME,
        "published_buy": o.buy if phase1 else o.published_buy, "published_mid": o.mid if phase1 else o.published_mid,
        "published_sell": o.sell if phase1 else o.published_sell,
        "published_currency": "ZMW" if phase1 else o.published_currency,
        "normalization_divisor": 1 if phase1 else o.normalization_divisor,
        "imported_at": imported_at,
    }


def save_monthly_averages(
    observations: list[MonthlyObservation], *, imported_at: datetime, engine: Engine | None = None,
) -> tuple[int, int]:
    """Atomically insert; returns (inserted, already_present). Never overwrites.

    Numerically equal buy/mid/sell AND matching stable canonical provenance are a no-op. Any rate or stable
    metadata difference is a conflict and rolls back the whole import. imported_at alone never creates a conflict.
    """
    inserted = identical = 0
    conflicts: list[date] = []
    with (engine or get_engine()).begin() as connection:
        for o in observations:
            params = row_params(o, imported_at)
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
            else:
                stored = connection.execute(_SELECT_ONE, params).mappings().one()
                if all(stored[field] == params[field] for field in _STABLE_COLUMNS):
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
