from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator


BOZ_SOURCE_URL = "https://www.boz.zm/markets-securities/boz-exchange-rates"


class FxQuote(BaseModel):
    """Published ZMW per USD rates. Decimals serialize as JSON strings."""

    symbol: Literal["USDZMW"] = "USDZMW"
    base_currency: Literal["USD"] = "USD"
    quote_currency: Literal["ZMW"] = "ZMW"
    buy: Decimal = Field(gt=0, allow_inf_nan=False)
    mid: Decimal = Field(gt=0, allow_inf_nan=False)
    sell: Decimal = Field(gt=0, allow_inf_nan=False)
    observed_at: AwareDatetime
    retrieved_at: AwareDatetime
    source: Literal["boz"] = "boz"
    source_url: Literal[BOZ_SOURCE_URL] = BOZ_SOURCE_URL
    stale: bool

    @field_validator("observed_at", "retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)
