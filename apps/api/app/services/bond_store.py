"""PostgreSQL persistence for normalized Government Bond auction rows (no raw payloads)."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.bonds import GovernmentBondAuction, GovernmentBondHistoryPoint

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


RANGE_DAYS: dict[str, int | None] = {"3M": 90, "6M": 182, "1Y": 365, "ALL": None}
_HISTORY = text(
    """
    SELECT tenor_years, auction_date, tender_number, isin, coupon_rate, yield_rate, source
    FROM government_bond_auctions
    WHERE tenor_years = :tenor_years AND (CAST(:since AS DATE) IS NULL OR auction_date >= :since)
    ORDER BY auction_date ASC, tender_number ASC
    """
)


def fetch_bond_history(
    tenor_years: int, range_: str, *, today: date, engine: Engine | None = None
) -> list[GovernmentBondHistoryPoint]:
    """Stored accepted observations only, ascending by auction date. Never deduplicated by ISIN."""
    days = RANGE_DAYS[range_]
    since = None if days is None else today - timedelta(days=days)
    with (engine or get_engine()).connect() as connection:
        rows = connection.execute(_HISTORY, {"tenor_years": tenor_years, "since": since}).mappings().all()
    return [GovernmentBondHistoryPoint(tenor=f"{r['tenor_years']}Y", **{k: r[k] for k in (
        "auction_date", "tender_number", "isin", "coupon_rate", "yield_rate", "source")}) for r in rows]
