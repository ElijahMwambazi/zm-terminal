from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, Field, field_validator

OilBenchmark = Literal["BRENT", "WTI"]
OIL_BENCHMARKS: tuple[str, ...] = ("BRENT", "WTI")
OilRange = Literal["1M", "3M", "1Y", "5Y", "ALL"]
OIL_SOURCE = "fred"
# Application heuristic, not a market-calendar or publisher rule: a daily series is "stale" when its newest
# observation is more than this many calendar days old (allows weekends, holidays and publication timing).
# The single source of truth; it is also returned in API responses so the UI never hardcodes it.
OIL_STALE_AFTER_DAYS = 14
OIL_SOURCE_UNIT = "Dollars per Barrel"  # FRED's units string; anything else is rejected, never converted


class OilSeries(NamedTuple):
    benchmark: str
    name: str
    series_id: str  # FRED series, validated against openbb_fred's SERIES_MAP
    openbb_commodity: str
    source_url: str


OIL_SERIES: dict[str, OilSeries] = {
    "BRENT": OilSeries("BRENT", "Brent crude oil (Europe)", "DCOILBRENTEU", "brent",
                       "https://fred.stlouisfed.org/series/DCOILBRENTEU"),
    "WTI": OilSeries("WTI", "WTI crude oil (Cushing, Oklahoma)", "DCOILWTICO", "wti",
                     "https://fred.stlouisfed.org/series/DCOILWTICO"),
}


class OilPoint(BaseModel):
    """One published daily benchmark value. Negative values are valid (WTI, 2020-04-20)."""

    benchmark: OilBenchmark
    observed_date: date  # the trading day the benchmark refers to (a date, not a timestamp)
    value: Decimal = Field(allow_inf_nan=False)
    currency: Literal["USD"] = "USD"
    unit: Literal["barrel"] = "barrel"
    source: Literal["fred"] = "fred"


class OilObservation(OilPoint):
    name: str
    series_id: str
    source_url: str
    retrieved_at: AwareDatetime
    stale: bool
    stale_after_days: int = OIL_STALE_AFTER_DAYS

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class OilLatest(BaseModel):
    """Newest live observation per benchmark; a benchmark with no recent upstream observation is listed as missing."""

    observations: list[OilObservation]
    missing_benchmarks: list[OilBenchmark]


class OilHistory(BaseModel):
    benchmark: OilBenchmark
    range: OilRange
    count: int
    observations: list[OilPoint]
