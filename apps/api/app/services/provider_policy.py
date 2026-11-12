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
    required_timestamps=("source", "observed_at", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "One request per user action, 10 s timeout, no retries, no background polling. "
        "Rates change at most a few times per day; do not poll more than about once a minute."
    ),
)

PROVIDER_POLICIES: dict[str, ProviderPolicy] = {BOZ_USD_POLICY.provider_id: BOZ_USD_POLICY}
