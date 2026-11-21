"""Source models for the BoZ commercial bank USD/ZMW daily rates (local/internal; rights review_required).

Normative contract: docs/research/boz-commercial-bank-fx.md. These are plain frozen records: the parser builds them, the store
persists them. Decimals are exact `Decimal`; the exact published strings are kept beside them and are never rebuilt from the numbers.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

LAYOUT_V1 = "v1"  # January 2026: 'Interbank Rates' / 'Retail Rates'
LAYOUT_V2 = "v2"  # November 2026: 'INTERBANK' / 'BOARD RATES'
LAYOUTS = (LAYOUT_V1, LAYOUT_V2)
# The source's own label for the retail pair, exactly as published (never normalised into one spelling).
RETAIL_SOURCE_LABEL = {LAYOUT_V1: "Retail Rates", LAYOUT_V2: "BOARD RATES"}
SLOTS = ("09:30", "12:30", "15:30")  # application identity of the source labels 9:30, 12:30, 15:30 (labels, not timestamps)
RIGHTS_STATUS = "review_required"
MARKET_AVERAGE_NAME = "market average"  # compared case-insensitively on the normalised name


class BankFxError(ValueError):
    """The source document violates the contract; nothing from it may be stored."""


@dataclass(frozen=True)
class BankFxRate:
    """One institution row of one slot of one snapshot (only for a slot the source published: all four cells present)."""

    source_row_index: int  # numeric CMS table-row key: preserves source order; never an identity
    slot: str
    institution_name_raw: str
    institution_name: str  # whitespace-collapsed and trimmed raw name: the identity inside a snapshot
    is_market_average: bool
    interbank_buy: Decimal
    interbank_buy_text: str
    interbank_sell: Decimal
    interbank_sell_text: str
    retail_buy: Decimal
    retail_buy_text: str
    retail_sell: Decimal
    retail_sell_text: str
    retail_source_label: str


@dataclass(frozen=True)
class BankFxSnapshot:
    """One immutable source node revision (JSON:API id + Drupal vid)."""

    observation_date: date  # calendar date of the raw value at the offset it was published with
    source_record_id: UUID
    source_revision_id: int
    source_internal_nid: int
    source_created: datetime
    source_changed: datetime
    source_revision_timestamp: datetime
    source_title: str  # untrusted provenance text, never an identity
    source_rates_datetime_raw: str  # exact JSON string received
    source_rates_datetime: datetime  # that string parsed to an aware instant
    table_layout: str
    retrieved_at: datetime
    source_url: str  # request-location metadata, not source content (excluded from same-revision comparison)
    rights_status: str
    rates: tuple[BankFxRate, ...]
