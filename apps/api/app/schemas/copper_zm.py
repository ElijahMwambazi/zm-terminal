from datetime import date, datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.schemas.btc import PlainDecimal
from app.schemas.copper import COPPER_SOURCE_PAGE_URL, COPPER_STALE_AFTER_MONTHS, CopperRange
from app.schemas.fx import BOZ_SOURCE_URL
from app.schemas.oil_zm import FxCoverage

COPPER_ZM_METHODOLOGY_ID = "copper_zm_v1"
COPPER_ZM_METHODOLOGY = ("IMF PCPS Copper monthly average × arithmetic mean of daily BoZ USD/ZMW mid averages "
                         "for stored observation days (each stored day weighted equally)")
COPPER_ZM_NOTICE = ("Derived analytical value. Not an official IMF, LME or Bank of Zambia series, not a Zambia copper price "
                    "and not an executable quote.")
# The two inputs are not simultaneous: stated neutrally in every detailed response.
COPPER_ZM_TIMING_NOTE = ("The IMF Copper monthly average and the BoZ FX observations represent different observation schedules "
                         "within the matched calendar month.")


class CopperZmPoint(BaseModel):
    """One derived calendar month: IMF Copper (USD/mt) x BoZ USD/ZMW observed-days mid average = ZMW/mt. Never an official series."""

    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    period_start: date
    period_end: date
    currency: Literal["ZMW"] = "ZMW"
    unit: Literal["metric_ton"] = "metric_ton"
    zmw_per_mt: PlainDecimal = Field(allow_inf_nan=False)  # unrounded product of the two components below
    copper_usd_per_mt: PlainDecimal = Field(allow_inf_nan=False)
    usd_zmw_observed_days_avg: PlainDecimal = Field(allow_inf_nan=False)  # mean of per-day means of stored BoZ mid over observed days (not a full-month average)
    fx_day_count: int
    fx_observation_count: int
    fx_coverage: FxCoverage


class CopperZmMonth(CopperZmPoint):
    methodology_id: str = COPPER_ZM_METHODOLOGY_ID
    methodology: str = COPPER_ZM_METHODOLOGY
    notice: str = COPPER_ZM_NOTICE
    timing_note: str = COPPER_ZM_TIMING_NOTE
    fx_first_date: date  # Africa/Lusaka calendar dates of the first and last FX day used
    fx_last_date: date
    fx_first_observation: AwareDatetime
    fx_last_observation: AwareDatetime
    copper_source: Literal["imf"] = "imf"
    copper_source_series: Literal["PCOPP"] = "PCOPP"
    copper_source_definition: str
    copper_source_url: str  # the exact IMF SDMX request that supplied the stored value
    copper_source_page_url: str = COPPER_SOURCE_PAGE_URL
    copper_retrieved_at: AwareDatetime
    fx_source: Literal["boz"] = "boz"
    fx_pair: Literal["USDZMW"] = "USDZMW"
    fx_price: Literal["mid"] = "mid"
    fx_source_url: str = BOZ_SOURCE_URL
    stale: bool
    stale_after_months: int = COPPER_STALE_AFTER_MONTHS

    @field_validator("fx_first_observation", "fx_last_observation", "copper_retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class CopperZmHistory(BaseModel):
    range: CopperRange
    count: int
    copper_months_without_fx: int  # stored Copper months in this range with no stored BoZ FX; omitted, never filled
    methodology_id: str = COPPER_ZM_METHODOLOGY_ID
    methodology: str = COPPER_ZM_METHODOLOGY
    notice: str = COPPER_ZM_NOTICE
    timing_note: str = COPPER_ZM_TIMING_NOTE
    observations: list[CopperZmPoint]
