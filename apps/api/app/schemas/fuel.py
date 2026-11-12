from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

ERB_SOURCE_URL = "https://www.erb.org.zm/petroleum/"
FuelProduct = Literal["PETROL", "DIESEL", "KEROSENE", "JET_A1", "LSGO"]
FUEL_PRODUCTS: tuple[str, ...] = ("PETROL", "DIESEL", "KEROSENE", "JET_A1", "LSGO")
PriceType = Literal["retail", "wholesale"]
FuelRange = Literal["1Y", "3Y", "5Y", "ALL"]


class FuelPrice(BaseModel):
    """A published ERB price for one pricing month (the archive has month granularity)."""

    product: FuelProduct
    effective_date: date  # first day of the pricing month
    price_zmw: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: Literal["ZMW"] = "ZMW"
    unit: Literal["litre"] = "litre"
    price_type: PriceType = "retail"
    source: Literal["erb"] = "erb"
    source_url: str
    retrieved_at: AwareDatetime

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class FuelLatest(BaseModel):
    """Newest stored retail price per product; products with none are listed as missing."""

    prices: list[FuelPrice]
    missing_products: list[FuelProduct]


class FuelHistoryPoint(BaseModel):
    product: FuelProduct
    effective_date: date
    price_zmw: Decimal
    unit: Literal["litre"] = "litre"
    price_type: PriceType
    source: Literal["erb"] = "erb"


class FuelHistory(BaseModel):
    product: FuelProduct
    price_type: PriceType
    range: FuelRange
    count: int
    observations: list[FuelHistoryPoint]
