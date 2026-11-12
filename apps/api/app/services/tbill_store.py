"""PostgreSQL persistence for normalized T-bill auction rows (no raw payloads)."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.tbills import TreasuryBillAuction, TreasuryBillHistoryPoint

_INSERT = text(
    """
    INSERT INTO treasury_bill_auctions
        (auction_date, tender_number, tenor_days, isin, discount_rate, yield_rate, source, source_url, retrieved_at)
    VALUES (:auction_date, :tender_number, :tenor_days, :isin, :discount_rate, :yield_rate, :source, :source_url, :retrieved_at)
    ON CONFLICT (auction_date, tender_number, tenor_days) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT isin, discount_rate, yield_rate FROM treasury_bill_auctions
    WHERE auction_date = :auction_date AND tender_number = :tender_number AND tenor_days = :tenor_days
    """
)


class TbillConflictError(Exception):
    """A stored auction row has different values than the incoming one for the same key."""


def save_tbill_auctions(rows: list[TreasuryBillAuction], *, engine: Engine | None = None) -> tuple[int, int]:
    """Atomically insert; returns (inserted, identical). Never overwrites; any conflict rolls back all."""
    inserted = identical = 0
    conflicts: list[str] = []
    with (engine or get_engine()).begin() as connection:
        for row in rows:
            params = row.model_dump()
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
                continue
            existing = connection.execute(_SELECT_ONE, params).one()
            if tuple(existing) == (row.isin, row.discount_rate, row.yield_rate):  # numeric compare
                identical += 1
            else:
                conflicts.append(f"{row.tender_number} {row.tenor}")
        if conflicts:
            raise TbillConflictError(f"{len(conflicts)} stored row(s) conflict with BoZ values, e.g. {conflicts[:5]}")
    return inserted, identical


RANGE_DAYS: dict[str, int | None] = {"1M": 30, "3M": 90, "6M": 182, "1Y": 365, "ALL": None}
_HISTORY = text(
    """
    SELECT tenor_days, auction_date, tender_number, isin, discount_rate, yield_rate, source
    FROM treasury_bill_auctions
    WHERE tenor_days = :tenor_days AND (CAST(:since AS DATE) IS NULL OR auction_date >= :since)
    ORDER BY auction_date ASC, tender_number ASC
    """
)


def fetch_tbill_history(
    tenor_days: int, range_: str, *, today: date, engine: Engine | None = None
) -> list[TreasuryBillHistoryPoint]:
    """Stored observations only, ascending by auction date. Never interpolated or filled."""
    days = RANGE_DAYS[range_]
    since = None if days is None else today - timedelta(days=days)
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"tenor_days": tenor_days, "since": since}).mappings().all()
    return [TreasuryBillHistoryPoint(tenor=f"{r['tenor_days']}D", **{k: r[k] for k in (
        "auction_date", "tender_number", "isin", "discount_rate", "yield_rate", "source")}) for r in rows]
