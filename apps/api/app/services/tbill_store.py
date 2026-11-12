"""PostgreSQL persistence for normalized T-bill auction rows (no raw payloads)."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.tbills import TreasuryBillAuction

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
