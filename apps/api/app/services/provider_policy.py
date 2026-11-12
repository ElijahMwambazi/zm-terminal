"""Minimal, reusable data-provider policy and provenance records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    dataset_id: str
    name: str
    source_url: str
    classification: Literal["public_market_data"]
    personal_data_expected: bool
    authentication: Literal["none", "api_key"]
    normalized_storage_allowed: bool
    raw_response_storage: Literal["disabled", "enabled"]
    attribution_required: bool
    required_provenance: tuple[str, ...]
    required_timestamps: tuple[str, ...]
    redistribution: Literal["review_required", "permitted"]
    commercial_use: Literal["review_required", "permitted"]
    request_guidance: str


BOZ_USD_POLICY = ProviderPolicy(
    provider_id="boz",
    dataset_id="boz_zmw_usd_daily_exchange_rates",
    name="Bank of Zambia",
    source_url="https://www.boz.zm/markets-securities/boz-exchange-rates",
    classification="public_market_data",
    personal_data_expected=False,
    authentication="none",
    normalized_storage_allowed=True,
    raw_response_storage="disabled",
    attribution_required=True,
    required_provenance=("source", "source_url"),
    required_timestamps=("observed_at", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "One request per user action, 10 s timeout, no retries, no background polling. "
        "Rates change about three times per business day. Manual historical backfill: "
        "sequential requests of at most 31 days, 2 s apart, at most 400 days per run."
    ),
)

# Same provider, separate dataset: only identity, source and guidance differ.
BOZ_TBILL_POLICY = BOZ_USD_POLICY.model_copy(update={
    "dataset_id": "boz_treasury_bill_auction_results",
    "source_url": "https://www.boz.zm/markets-securities/treasury-bills",
    "required_timestamps": ("auction_date", "retrieved_at"),
    "request_guidance": (
        "One request per user action to the BoZ JSON:API tbills_auction_results (5 most recent "
        "tenders), 10 s timeout, no retries, no background polling. Tenders are roughly fortnightly. "
        "PDFs are not fetched."
    ),
})

BOZ_BOND_POLICY = BOZ_USD_POLICY.model_copy(update={
    "dataset_id": "boz_government_bond_auction_results",
    "source_url": "https://www.boz.zm/markets-securities/bonds",
    "required_timestamps": ("auction_date", "retrieved_at"),
    "request_guidance": (
        "Per user action, sequential requests to the BoZ JSON:API bond_auction, newest first, "
        "50 auctions per page following links.next, stopping once every tenor is found, at the "
        "archive end, or failing closed at 10 pages (the archive is ~3 pages). 10 s timeout, no retries, no background polling. "
        "Auctions are irregular (21 to 63 days apart in 2026). PDFs are not fetched."
    ),
})

# Keyed by dataset: one provider may publish several datasets.
PROVIDER_POLICIES: dict[str, ProviderPolicy] = {
    p.dataset_id: p for p in (BOZ_USD_POLICY, BOZ_TBILL_POLICY, BOZ_BOND_POLICY)
}
