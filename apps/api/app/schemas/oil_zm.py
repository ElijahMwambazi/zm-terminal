from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.schemas.fx import BOZ_SOURCE_URL
from app.schemas.oil import OIL_SOURCE_PAGE_URL, OIL_STALE_AFTER_MONTHS, OilRange

OIL_ZM_METHODOLOGY_ID = "oil_zm_v1"
OIL_ZM_METHODOLOGY = "World Bank Brent monthly average × arithmetic mean of daily BoZ USD/ZMW mid averages"
OIL_ZM_NOTICE = "Derived analytical value. Not a Zambia retail fuel price or an official published series."
# partial_source_window: the stored BoZ history begins after the first calendar day of this month, so the month's FX
# average covers only part of it. observed_month: every other month; this does NOT claim that every business day is present.
FxCoverage = Literal["partial_source_window", "observed_month"]


class OilZmPoint(BaseModel):
    """One derived calendar month: Brent (USD/bbl) x BoZ USD/ZMW monthly mid average = ZMW/bbl. Never an official series."""

    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    period_start: date
    period_end: date
    currency: Literal["ZMW"] = "ZMW"
    unit: Literal["barrel"] = "barrel"
    zmw_per_bbl: Decimal = Field(allow_inf_nan=False)  # unrounded product of the two components below
    brent_usd_per_bbl: Decimal = Field(allow_inf_nan=False)
    usd_zmw_monthly_avg: Decimal = Field(allow_inf_nan=False)  # mean of per-day means of BoZ mid, ZMW per USD
    fx_day_count: int
    fx_observation_count: int
    fx_coverage: FxCoverage


class OilZmMonth(OilZmPoint):
    methodology_id: str = OIL_ZM_METHODOLOGY_ID
    methodology: str = OIL_ZM_METHODOLOGY
    notice: str = OIL_ZM_NOTICE
    fx_first_date: date  # Africa/Lusaka calendar dates of the first and last FX day used
    fx_last_date: date
    fx_first_observation: AwareDatetime
    fx_last_observation: AwareDatetime
    brent_source: Literal["world_bank"] = "world_bank"
    brent_source_url: str  # exact release workbook (machine provenance)
    brent_source_page_url: str = OIL_SOURCE_PAGE_URL
    brent_publication_date: date
    fx_source: Literal["boz"] = "boz"
    fx_pair: Literal["USDZMW"] = "USDZMW"
    fx_price: Literal["mid"] = "mid"
    fx_source_url: str = BOZ_SOURCE_URL
    stale: bool
    stale_after_months: int = OIL_STALE_AFTER_MONTHS

    @field_validator("fx_first_observation", "fx_last_observation")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class OilZmHistory(BaseModel):
    range: OilRange
    count: int
    brent_months_without_fx: int  # stored Brent months in this range with no stored BoZ FX; omitted, never filled
    methodology_id: str = OIL_ZM_METHODOLOGY_ID
    methodology: str = OIL_ZM_METHODOLOGY
    observations: list[OilZmPoint]
