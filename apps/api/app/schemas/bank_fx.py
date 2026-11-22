"""Stored-only commercial bank USD/ZMW rates (BoZ `commercial_bank_daily_rates`). Local/internal: `rights_status` is a workflow state
(currently `review_required`), never permission to redistribute.

Rates are the exact strings the source published and the database stored (checked against the NUMERIC value on insert): '9.9' stays '9.9',
never 9.9 and never '9.9000'. Nothing here is derived: no spreads, rankings, averages or aliases. `market_average` is the source's own
published row, or null when the source did not publish it for that slot. Slot labels are the source's column labels, not timestamps.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

BankFxSlot = Literal["09:30", "12:30", "15:30"]
BankFxLayout = Literal["v1", "v2"]
BANK_FX_SLOTS: tuple[str, ...] = ("09:30", "12:30", "15:30")

_RATE = {"pattern": r"^[0-9]+\.[0-9]{1,4}$"}


class BankFxInstitutionRate(BaseModel):
    """One published institution x slot row, in the source table's row order."""

    source_row_index: int = Field(ge=3)
    institution_name: str = Field(min_length=1)  # whitespace-collapsed identity within the snapshot
    institution_name_raw: str = Field(min_length=1)  # exactly as published
    interbank_buy: str = Field(**_RATE)
    interbank_sell: str = Field(**_RATE)
    retail_buy: str = Field(**_RATE)
    retail_sell: str = Field(**_RATE)


class BankFxSlotSnapshot(BaseModel):
    slot: BankFxSlot
    retail_source_label: str = Field(min_length=1)  # the source's own label for the retail pair ('Retail Rates' or 'BOARD RATES')
    banks: list[BankFxInstitutionRate]  # source-row order; the market average is never in this list
    market_average: BankFxInstitutionRate | None = None


class BankFxSnapshotRead(BaseModel):
    observation_date: date
    source_record_id: str
    source_revision_id: int
    source_internal_nid: int
    source_created: datetime
    source_changed: datetime
    source_revision_timestamp: datetime
    source_title: str
    source_rates_datetime_raw: str
    source_rates_datetime: datetime
    table_layout: BankFxLayout
    retrieved_at: datetime
    source_url: str
    rights_status: str
    slots: list[BankFxSlotSnapshot]  # only slots the source published, chronological; an unpublished slot is absent


class BankFxLatest(BaseModel):
    """`snapshot` is the canonical snapshot of the newest observation date STORED locally (not necessarily BoZ's newest publication)."""

    snapshot: BankFxSnapshotRead | None = None
