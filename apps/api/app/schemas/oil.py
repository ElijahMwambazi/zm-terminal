import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

OilBenchmark = Literal["BRENT", "WTI"]
OIL_BENCHMARKS: tuple[str, ...] = ("BRENT", "WTI")
OilRange = Literal["1Y", "3Y", "5Y", "ALL"]
OIL_SOURCE_PAGE_URL = "https://www.worldbank.org/en/research/commodity-markets"  # stable human-facing page
OIL_SOURCE_UNIT = "($/bbl)"  # the workbook's unit label; any other label is rejected, never converted
# Application heuristic, not a publisher SLA: the Pink Sheet is updated monthly (about the 2nd business day), so the
# newest published month is normally the previous calendar month. "Stale" means the newest stored month is more than
# this many calendar months before the current month. The single source of truth; it is returned in API responses
# so the UI never hardcodes it.
OIL_STALE_AFTER_MONTHS = 2


class OilSeries(NamedTuple):
    benchmark: str
    name: str
    description: str  # from the workbook's own "Description" sheet
    source_series: str  # exact workbook column label


OIL_SERIES: dict[str, OilSeries] = {
    "BRENT": OilSeries("BRENT", "Brent crude oil", "Crude oil, U.K. Brent 38° API", "Crude oil, Brent"),
    "WTI": OilSeries("WTI", "WTI crude oil", "Crude oil, U.S. West Texas Intermediate (WTI) 40° API", "Crude oil, WTI"),
}


class OilPoint(BaseModel):
    """One published calendar-month average. period_start/period_end are the month's first and last day."""

    benchmark: OilBenchmark
    period_start: date
    period_end: date
    value: Decimal = Field(allow_inf_nan=False)
    currency: Literal["USD"] = "USD"
    unit: Literal["barrel"] = "barrel"
    source: Literal["world_bank"] = "world_bank"
    price_basis: Literal["monthly_average"] = "monthly_average"

    @model_validator(mode="after")
    def calendar_month(self) -> "OilPoint":
        last = calendar.monthrange(self.period_start.year, self.period_start.month)[1]
        if self.period_start.day != 1 or self.period_end != self.period_start.replace(day=last):
            raise ValueError("Oil period must be exactly one calendar month")
        return self


class OilObservation(OilPoint):
    name: str
    description: str
    source_series: str
    source_url: str  # the exact release workbook URL (machine provenance; the hash changes every release)
    source_page_url: str = OIL_SOURCE_PAGE_URL  # stable page for human navigation
    publication_date: date
    retrieved_at: AwareDatetime
    stale: bool
    stale_after_months: int = OIL_STALE_AFTER_MONTHS

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class OilLatest(BaseModel):
    """Newest stored month per benchmark; a benchmark with nothing stored is listed as missing."""

    observations: list[OilObservation]
    missing_benchmarks: list[OilBenchmark]


class OilHistory(BaseModel):
    benchmark: OilBenchmark
    range: OilRange
    count: int
    observations: list[OilPoint]
