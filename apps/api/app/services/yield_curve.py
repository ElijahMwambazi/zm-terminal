"""Zambia sovereign published-auction-yield tenor curve: a derived snapshot from stored BoZ auction rows (nothing persisted).

For each supported tenor (91D 182D 273D 364D 2Y 3Y 5Y 7Y 10Y 15Y) this selects, independently, the latest stored
accepted observation whose auction_date is on or before the as-of date. Ties on one auction date (rare: the stored key
allows two tender numbers) go to the higher tender number, deterministically. Points normally come from different
auction dates; that is expected and is reported, not hidden.

What it is not: the stored bond rows hold the published tenor bucket, ISIN, coupon and yield, but no maturity date, and
BoZ reopens existing bonds (same ISIN and maturity, a new yield). So tenors are used exactly as published; no remaining
maturity, maturity date, discount factor, spot/forward rate, fit or interpolated tenor is ever derived. The yield used is
`yield_rate` (never the T-bill discount rate or the bond coupon). Rejected source tenders are not stored in these
histories, so this is the latest stored accepted observation, not necessarily the latest BoZ auction. Reads PostgreSQL
only: no HTTP, no OpenBB, no writes.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.yield_curve import (
    BOND_YEARS, CURVE_TENORS, TBILL_DAYS, GovernmentBondCurvePoint, TreasuryBillCurvePoint, YieldCurve,
)

_TBILLS = text(
    """
    SELECT tenor_days, auction_date, tender_number, isin, discount_rate, yield_rate, source_url
    FROM treasury_bill_auctions WHERE auction_date <= :as_of
    """
)
_BONDS = text(
    """
    SELECT tenor_years, auction_date, tender_number, isin, coupon_rate, yield_rate, source_url
    FROM government_bond_auctions WHERE auction_date <= :as_of
    """
)


def _newest_per_tenor(rows: list[Mapping[str, Any]], tenor_key: str, labels: dict[int, str], as_of: date) -> dict[str, Mapping[str, Any]]:
    """Newest row per tenor on or before as_of; later auction dates are ignored, never used."""
    chosen: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        label = labels.get(row[tenor_key])
        if label is None or row["auction_date"] > as_of:
            continue
        best = chosen.get(label)
        if best is None or (row["auction_date"], row["tender_number"]) > (best["auction_date"], best["tender_number"]):
            chosen[label] = row
    return chosen


def derive_yield_curve(
    tbill_rows: list[Mapping[str, Any]], bond_rows: list[Mapping[str, Any]], *, as_of: date,
) -> YieldCurve | None:
    """Pure selection and assembly; None when no tenor has an observation on or before as_of."""
    bills = _newest_per_tenor(tbill_rows, "tenor_days", TBILL_DAYS, as_of)
    bonds = _newest_per_tenor(bond_rows, "tenor_years", BOND_YEARS, as_of)
    points = []
    for order, tenor in enumerate(CURVE_TENORS, start=1):
        if tenor in bills:
            row = bills[tenor]
            points.append(TreasuryBillCurvePoint(
                tenor=tenor, tenor_order=order, yield_rate=row["yield_rate"], auction_date=row["auction_date"],
                tender_number=row["tender_number"], isin=row["isin"], source_url=row["source_url"],
                age_days=(as_of - row["auction_date"]).days, discount_rate=row["discount_rate"]))
        elif tenor in bonds:
            row = bonds[tenor]
            points.append(GovernmentBondCurvePoint(
                tenor=tenor, tenor_order=order, yield_rate=row["yield_rate"], auction_date=row["auction_date"],
                tender_number=row["tender_number"], isin=row["isin"], source_url=row["source_url"],
                age_days=(as_of - row["auction_date"]).days, coupon_rate=row["coupon_rate"]))
    if not points:
        return None
    dates = [p.auction_date for p in points]
    present = {p.tenor for p in points}
    return YieldCurve(
        as_of=as_of, point_count=len(points), missing_tenors=[t for t in CURVE_TENORS if t not in present],
        oldest_point_date=min(dates), newest_point_date=max(dates), mixed_vintage=len(set(dates)) > 1,
        vintage_span_days=(max(dates) - min(dates)).days, points=points)


def fetch_yield_curve(as_of: date, *, engine: Engine | None = None) -> YieldCurve | None:
    """Read the two stored histories (rows on or before as_of) and derive the snapshot. Read-only."""
    with (engine or get_engine()).connect() as connection:
        tbills = connection.execute(_TBILLS, {"as_of": as_of}).mappings().all()
        bonds = connection.execute(_BONDS, {"as_of": as_of}).mappings().all()
    return derive_yield_curve(list(tbills), list(bonds), as_of=as_of)
