from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

TBILL_SOURCE_URL = "https://www.boz.zm/markets-securities/treasury-bills"
Tenor = Literal["91D", "182D", "273D", "364D"]
TENORS: tuple[str, ...] = ("91D", "182D", "273D", "364D")


class RejectedTender(BaseModel):
    """A fetched tender that failed validation; fields are None when not readable."""

    tender_number: str | None
    auction_date: date | None
    # True if the tender carries no result records at all (e.g. an empty legacy placeholder).
    no_results: bool = False


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
    # Rejected tenders dated after this row's auction: any of them may have carried this
    # tenor, so a non-empty list means this value is a fallback. Empty means no such tender.
    newer_rejected_tenders: list[str] = []

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class TreasuryBillsLatest(BaseModel):
    """Newest published value per tenor, each with its own auction date."""

    tenors: list[TreasuryBillAuction]
    missing_tenors: list[Tenor]
    # Every recent BoZ tender that failed validation and was ignored ("unknown" if its number
    # was unreadable). Informational: whether a row is a fallback is `newer_rejected_tenders`.
    skipped_tenders: list[str]
