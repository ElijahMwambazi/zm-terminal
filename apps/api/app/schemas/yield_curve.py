from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

CURVE_TYPE = "published_auction_yield_tenor_curve"
CURVE_METHODOLOGY = (
    "Latest stored accepted BoZ auction yield for each published Treasury-bill and Government-bond tenor "
    "at or before the as-of date."
)
CURVE_NOTICE = (
    "Derived from BoZ auction results. Points may come from different auction dates. "
    "Bond tenors are published tenor buckets, not calculated remaining maturities."
)
YIELD_UNIT = "percent_per_annum_as_published"

# Fixed display order of the published tenor buckets. This is a categorical sequence, not a maturity axis: bond
# tenors are BoZ's published labels and the stored data holds no maturity dates, so no remaining maturity is known.
CURVE_TENORS: tuple[str, ...] = ("91D", "182D", "273D", "364D", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y")
CurveTenor = Literal["91D", "182D", "273D", "364D", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
TBILL_DAYS: dict[int, str] = {91: "91D", 182: "182D", 273: "273D", 364: "364D"}
BOND_YEARS: dict[int, str] = {2: "2Y", 3: "3Y", 5: "5Y", 7: "7Y", 10: "10Y", 15: "15Y"}


class _CurvePoint(BaseModel):
    """One stored accepted BoZ auction observation, used as published (yield_rate), with its own auction date."""

    tenor: CurveTenor  # the published tenor bucket exactly as stored
    tenor_order: int  # 1..10 position in the fixed display sequence (categorical; NOT a maturity or remaining maturity)
    yield_rate: Decimal = Field(allow_inf_nan=False)  # percent per annum as published (not divided by 100)
    yield_unit: Literal["percent_per_annum_as_published"] = YIELD_UNIT
    auction_date: date
    tender_number: str
    isin: str
    source: Literal["boz"] = "boz"
    source_url: str
    age_days: int  # as_of minus auction_date in calendar days (>= 0); only "days since this observation"


class TreasuryBillCurvePoint(_CurvePoint):
    instrument_type: Literal["treasury_bill"] = "treasury_bill"
    discount_rate: Decimal = Field(allow_inf_nan=False)  # supporting metadata only; the curve uses yield_rate


class GovernmentBondCurvePoint(_CurvePoint):
    instrument_type: Literal["government_bond"] = "government_bond"
    coupon_rate: Decimal = Field(allow_inf_nan=False)  # supporting metadata only; the curve uses yield_rate


CurvePoint = Annotated[Union[TreasuryBillCurvePoint, GovernmentBondCurvePoint], Field(discriminator="instrument_type")]


class YieldCurve(BaseModel):
    """A DERIVED snapshot: for each tenor, the latest stored accepted BoZ auction observation on or before `as_of`.

    Not an official BoZ curve and not a bootstrapped, fitted, spot, par or constant-maturity curve. Points usually come
    from different auction dates (`mixed_vintage`); `as_of` is only the cutoff date, never a common observation date.
    """

    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    curve_type: Literal["published_auction_yield_tenor_curve"] = CURVE_TYPE
    methodology: str = CURVE_METHODOLOGY
    notice: str = CURVE_NOTICE
    as_of: date
    yield_unit: Literal["percent_per_annum_as_published"] = YIELD_UNIT
    point_count: int
    missing_tenors: list[CurveTenor]  # no stored accepted observation on or before as_of; never interpolated or substituted
    oldest_point_date: date
    newest_point_date: date
    mixed_vintage: bool  # True when the selected points have more than one auction date
    vintage_span_days: int  # newest_point_date - oldest_point_date
    points: list[CurvePoint]
