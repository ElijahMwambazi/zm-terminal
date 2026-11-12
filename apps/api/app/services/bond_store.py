"""PostgreSQL persistence for normalized Government Bond auction rows (no raw payloads)."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.bonds import GovernmentBondAuction

_INSERT = text(
    """
    INSERT INTO government_bond_auctions
        (auction_date, tender_number, tenor_years, isin, coupon_rate, yield_rate, source, source_url, retrieved_at)
    VALUES (:auction_date, :tender_number, :tenor_years, :isin, :coupon_rate, :yield_rate, :source, :source_url, :retrieved_at)
    ON CONFLICT (auction_date, tender_number, tenor_years) DO NOTHING
    """
)
_SELECT_ONE = text(
    """
    SELECT isin, coupon_rate, yield_rate FROM government_bond_auctions
    WHERE auction_date = :auction_date AND tender_number = :tender_number AND tenor_years = :tenor_years
    """
)


class BondConflictError(Exception):
    """A stored auction row has different values than the incoming one for the same key."""


def save_bond_auctions(rows: list[GovernmentBondAuction], *, engine: Engine | None = None) -> tuple[int, int]:
    """Atomically insert; returns (inserted, identical). Never overwrites; any conflict rolls back all.

    The key is auction identity, not ISIN: reopened bonds legitimately repeat an ISIN.
    """
    inserted = identical = 0
    conflicts: list[str] = []
    with (engine or get_engine()).begin() as connection:
        for row in rows:
            params = row.model_dump()
            if connection.execute(_INSERT, params).rowcount == 1:
                inserted += 1
                continue
            existing = connection.execute(_SELECT_ONE, params).one()
            if tuple(existing) == (row.isin, row.coupon_rate, row.yield_rate):  # numeric compare
                identical += 1
            else:
                conflicts.append(f"{row.tender_number} {row.tenor}")
        if conflicts:
            raise BondConflictError(f"{len(conflicts)} stored row(s) conflict with BoZ values, e.g. {conflicts[:5]}")
    return inserted, identical
