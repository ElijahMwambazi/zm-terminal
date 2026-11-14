"""Published-tenor yield spreads: differences between points of an already-derived yield-curve snapshot.

Pure arithmetic on the snapshot returned by `derive_yield_curve` / `fetch_yield_curve`: there is no second selection
policy, no database access, no HTTP, no persistence. spread_bps = (long yield - short yield) * 100 in exact Decimal (no
rounding, no float). A spread whose endpoint tenor has no stored observation is listed as missing, never substituted,
interpolated or zero-filled. No interpretation (inversion, steepness, regime) is attached; the numbers and the two
source auction dates are exposed so a mixed-vintage spread is never mistaken for a same-date market spread.
"""

from app.schemas.yield_curve import YieldCurve
from app.schemas.yield_spreads import (
    SPREAD_DEFINITIONS, MissingYieldSpread, YieldSpreadPoint, YieldSpreads,
)

BASIS_POINTS_PER_PERCENTAGE_POINT = 100


def derive_yield_spreads(curve: YieldCurve) -> YieldSpreads:
    by_tenor = {point.tenor: point for point in curve.points}
    available: list[YieldSpreadPoint] = []
    missing: list[MissingYieldSpread] = []
    for spread_id, short_tenor, long_tenor in SPREAD_DEFINITIONS:
        short, long = by_tenor.get(short_tenor), by_tenor.get(long_tenor)
        if short is None or long is None:
            missing.append(MissingYieldSpread(
                spread_id=spread_id, short_tenor=short_tenor, long_tenor=long_tenor,
                missing_tenors=[t for t, p in ((short_tenor, short), (long_tenor, long)) if p is None]))
            continue
        available.append(YieldSpreadPoint(
            spread_id=spread_id, short_tenor=short_tenor, long_tenor=long_tenor,
            short_yield_rate=short.yield_rate, long_yield_rate=long.yield_rate,
            spread_bps=(long.yield_rate - short.yield_rate) * BASIS_POINTS_PER_PERCENTAGE_POINT,
            short_auction_date=short.auction_date, long_auction_date=long.auction_date,
            same_vintage=short.auction_date == long.auction_date,
            vintage_gap_days=abs((long.auction_date - short.auction_date).days)))
    return YieldSpreads(as_of=curve.as_of, available_spreads=available, missing_spreads=missing)
