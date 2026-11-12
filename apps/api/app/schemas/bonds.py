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
    # Tenders with a rejected record dated after this row's auction that could have carried
    # THIS tenor (that tenor's own record, or a tender-level rejection). Non-empty means this
    # value is a fallback. Rejections of other tenors never appear here.
    newer_rejected_tenders: list[str] = []

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class RejectedBondObservation(BaseModel):
    """Internal metadata for a rejected source record (never carries error text or raw values).

    scope "tender": the whole tender was untrustworthy, `affected_tenors` is None (any tenor).
    scope "tenor": one record failed; `affected_tenors` is that tenor, or None when the
    record's tenor could not be determined.
    """

    tender_number: str | None
    auction_date: date | None
    affected_tenors: tuple[BondTenor, ...] | None
    scope: Literal["tender", "tenor"]

    def could_affect(self, tenor: str) -> bool:
        return self.affected_tenors is None or tenor in self.affected_tenors


class GovernmentBondsLatest(BaseModel):
    """Newest published value per tenor, each with its own auction date."""

    tenors: list[GovernmentBondAuction]
    missing_tenors: list[BondTenor]
    # Missing tenors for which a rejected source record could have carried the value.
    unverified_tenors: list[BondTenor]
    # Tender numbers with any rejected record ("unknown" if unreadable). Informational:
    # whether a row is a fallback is its own `newer_rejected_tenders`.
    skipped_tenders: list[str]


BondHistoryRange = Literal["3M", "6M", "1Y", "ALL"]


class GovernmentBondHistoryPoint(BaseModel):
    """One stored accepted auction observation. A reopened ISIN yields one point per auction."""

    tenor: BondTenor
    auction_date: date
    tender_number: str
    isin: str
    coupon_rate: Decimal
    yield_rate: Decimal
    source: Literal["boz"] = "boz"


class GovernmentBondHistory(BaseModel):
    tenor: BondTenor
    range: BondHistoryRange
    count: int
    observations: list[GovernmentBondHistoryPoint]
