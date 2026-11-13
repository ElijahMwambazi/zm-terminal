from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

AgricultureCommodity = Literal["MAIZE", "SOYA_BEANS"]
AGRICULTURE_COMMODITIES: tuple[str, ...] = ("MAIZE", "SOYA_BEANS")
AgricultureRange = Literal["1Y", "3Y", "ALL"]
MarketType = Literal["spot", "national_average", "procurement", "government_sale"]
PriceBasis = Literal["indicative_average", "reported_trade", "reference", "announced_price"]


class AgriculturePrice(BaseModel):
    commodity: AgricultureCommodity
    market_type: Literal["national_average"] = "national_average"
    provider: Literal["moa"] = "moa"
    period_start: date
    period_end: date
    publication_date: date | None
    publication_date_source: str | None
    grade: str | None = None
    variety: str | None = None
    location_scope: Literal["national"] = "national"
    location: str | None = None
    price_basis: Literal["indicative_average"] = "indicative_average"
    source_price: Decimal = Field(gt=0, allow_inf_nan=False)
    source_currency: Literal["ZMW"] = "ZMW"
    source_unit: Literal["kg"] = "kg"
    normalized_zmw_per_kg: Decimal = Field(gt=0, allow_inf_nan=False)
    source: str
    source_url: str
    source_period_label: str
    retrieved_at: AwareDatetime

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class AgricultureLatest(BaseModel):
    prices: list[AgriculturePrice]
    missing_commodities: list[AgricultureCommodity]


class AgricultureHistoryPoint(BaseModel):
    commodity: AgricultureCommodity
    period_start: date
    period_end: date
    source_price: Decimal
    normalized_zmw_per_kg: Decimal
    source_unit: Literal["kg"] = "kg"
    price_basis: Literal["indicative_average"] = "indicative_average"
    source: str
    source_url: str


class AgricultureHistory(BaseModel):
    commodity: AgricultureCommodity
    market_type: Literal["national_average"] = "national_average"
    range: AgricultureRange
    count: int
    observations: list[AgricultureHistoryPoint]
