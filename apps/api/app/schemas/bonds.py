from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

BOND_SOURCE_URL = "https://www.boz.zm/markets-securities/bonds"
BondTenor = Literal["2Y", "3Y", "5Y", "7Y", "10Y", "15Y"]
BOND_TENORS: tuple[str, ...] = ("2Y", "3Y", "5Y", "7Y", "10Y", "15Y")


class GovernmentBondAuction(BaseModel):
    """One tenor of one BoZ bond auction. Rates are percent per annum as published (decimal strings).

    The same ISIN can appear in many auctions (reopenings); a row's identity is
    auction_date + tender_number + tenor, never the ISIN.
    """

    tenor: BondTenor
    tenor_years: Literal[2, 3, 5, 7, 10, 15]
    auction_date: date
    tender_number: str
    isin: str
    coupon_rate: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)
    yield_rate: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)
    source: Literal["boz"] = "boz"
    source_url: Literal[BOND_SOURCE_URL] = BOND_SOURCE_URL
    retrieved_at: AwareDatetime
    # Rejected auctions dated after this row's auction; non-empty means this value is a
    # fallback (one of them may have carried this tenor). Empty means no such auction.
    newer_rejected_tenders: list[str] = []

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class GovernmentBondsLatest(BaseModel):
    """Newest published value per tenor, each with its own auction date."""

    tenors: list[GovernmentBondAuction]
    missing_tenors: list[BondTenor]
    # Every recent auction that failed validation ("unknown" if its number was unreadable).
    # Informational: whether a row is a fallback is `newer_rejected_tenders`.
    skipped_tenders: list[str]
