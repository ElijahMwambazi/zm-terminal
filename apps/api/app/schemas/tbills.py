from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

TBILL_SOURCE_URL = "https://www.boz.zm/markets-securities/treasury-bills"
Tenor = Literal["91D", "182D", "273D", "364D"]
TENORS: tuple[str, ...] = ("91D", "182D", "273D", "364D")


class TreasuryBillAuction(BaseModel):
    """One tenor of one BoZ auction. Rates are percent per annum as published (decimal strings)."""

    tenor: Tenor
    tenor_days: Literal[91, 182, 273, 364]
    auction_date: date
    tender_number: str
    isin: str
    discount_rate: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)
    yield_rate: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)
    source: Literal["boz"] = "boz"
    source_url: Literal[TBILL_SOURCE_URL] = TBILL_SOURCE_URL
    retrieved_at: AwareDatetime
    stale: bool = False

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class TreasuryBillsLatest(BaseModel):
    """Newest published value per tenor, each with its own auction date."""

    tenors: list[TreasuryBillAuction]
    missing_tenors: list[Tenor]
    # Recent BoZ tenders that failed validation and were ignored (never partially used).
    skipped_tenders: list[str]
