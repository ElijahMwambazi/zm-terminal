from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.yield_curve import CURVE_TYPE, CurveTenor

SPREAD_METRIC_TYPE = "published_tenor_yield_spread"
SPREAD_METHODOLOGY = (
    "Difference between two yields selected from the derived Zambia sovereign published-auction-yield tenor curve, "
    "expressed in basis points."
)
SPREAD_NOTICE = (
    "Underlying tenor points may come from different BoZ auction dates; this is not necessarily a same-date market spread."
)

SpreadId = Literal["10Y_364D", "15Y_364D", "10Y_2Y", "15Y_91D"]
# Fixed, deliberately small set: (id, short tenor, long tenor). spread = long yield - short yield.
SPREAD_DEFINITIONS: tuple[tuple[SpreadId, CurveTenor, CurveTenor], ...] = (
    ("10Y_364D", "364D", "10Y"),
    ("15Y_364D", "364D", "15Y"),
    ("10Y_2Y", "2Y", "10Y"),
    ("15Y_91D", "91D", "15Y"),
)


class YieldSpreadPoint(BaseModel):
    """long_yield_rate minus short_yield_rate, in basis points, between two points of the derived curve snapshot."""

    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    metric_type: Literal["published_tenor_yield_spread"] = SPREAD_METRIC_TYPE
    spread_id: SpreadId
    short_tenor: CurveTenor  # published tenor buckets, not remaining maturities
    long_tenor: CurveTenor
    short_yield_rate: Decimal = Field(allow_inf_nan=False)  # percent per annum as published
    long_yield_rate: Decimal = Field(allow_inf_nan=False)
    spread_bps: Decimal = Field(allow_inf_nan=False)  # (long - short) * 100, exact Decimal; may be negative or zero
    short_auction_date: date
    long_auction_date: date
    same_vintage: bool  # True when both points come from the same auction date
    vintage_gap_days: int  # abs(long_auction_date - short_auction_date) in calendar days
    yield_unit: Literal["percent_per_annum_as_published"] = "percent_per_annum_as_published"
    spread_unit: Literal["basis_points"] = "basis_points"


class MissingYieldSpread(BaseModel):
    """A spread that cannot be computed because an endpoint tenor has no stored observation; nothing is substituted."""

    spread_id: SpreadId
    short_tenor: CurveTenor
    long_tenor: CurveTenor
    missing_tenors: list[CurveTenor]


class YieldSpreads(BaseModel):
    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    metric_type: Literal["published_tenor_yield_spread"] = SPREAD_METRIC_TYPE
    curve_type: Literal["published_auction_yield_tenor_curve"] = CURVE_TYPE
    methodology: str = SPREAD_METHODOLOGY
    notice: str = SPREAD_NOTICE
    as_of: date
    yield_unit: Literal["percent_per_annum_as_published"] = "percent_per_annum_as_published"
    spread_unit: Literal["basis_points"] = "basis_points"
    available_spreads: list[YieldSpreadPoint]
    missing_spreads: list[MissingYieldSpread]
