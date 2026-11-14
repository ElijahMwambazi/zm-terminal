import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.schemas.btc import PlainDecimal

CopperRange = Literal["1Y", "3Y", "5Y", "ALL"]
COPPER_RANGES: tuple[str, ...] = ("1Y", "3Y", "5Y", "ALL")
COPPER_SERIES = "PCOPP"
# The IMF's own description of series PCOPP (code description in DSD_PCPS, read 2026-11-14). The IMF is the provider; the
# definition names LME as the underlying benchmark source. Never shorten this into "LME Copper".
COPPER_SOURCE_DEFINITION = "Copper, grade A cathode, LME spot price, CIF European ports, US$ per metric ton"
COPPER_SOURCE_PAGE_URL = "https://data.imf.org/en/datasets/IMF.RES:PCPS"  # stable human-facing page
# Application heuristic, not an IMF SLA: the PCPS is updated monthly, so the newest published month is normally the previous
# calendar month. "Stale" means the newest stored month is more than this many calendar months before the current month
# (Africa/Lusaka). Returned in API responses so the UI never hardcodes it.
COPPER_STALE_AFTER_MONTHS = 2


class CopperPoint(BaseModel):
    """One published calendar-month average (nominal USD per metric ton). period_start/period_end are the month's first and last day."""

    benchmark: Literal["COPPER"] = "COPPER"
    period_start: date
    period_end: date
    value: PlainDecimal = Field(allow_inf_nan=False)
    currency: Literal["USD"] = "USD"
    unit: Literal["metric_ton"] = "metric_ton"
    source: Literal["imf"] = "imf"
    price_basis: Literal["monthly_average"] = "monthly_average"

    @model_validator(mode="after")
    def calendar_month(self) -> "CopperPoint":
        last = calendar.monthrange(self.period_start.year, self.period_start.month)[1]
        if self.period_start.day != 1 or self.period_end != self.period_start.replace(day=last):
            raise ValueError("Copper period must be exactly one calendar month")
        return self


class CopperObservation(CopperPoint):
    """The newest stored month with provenance: a stored monthly benchmark, not a live price."""

    source_series: Literal["PCOPP"] = COPPER_SERIES
    source_definition: str
    source_url: str  # the exact IMF SDMX request that supplied the stored value
    source_page_url: str = COPPER_SOURCE_PAGE_URL
    retrieved_at: AwareDatetime
    stale: bool
    stale_after_months: int = COPPER_STALE_AFTER_MONTHS

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class CopperLatest(BaseModel):
    """Latest stored monthly Copper benchmark; `observation` is null (HTTP 200) when nothing is stored yet."""

    observation: CopperObservation | None


class CopperHistory(BaseModel):
    benchmark: Literal["COPPER"] = "COPPER"
    range: CopperRange
    count: int
    observations: list[CopperPoint]
