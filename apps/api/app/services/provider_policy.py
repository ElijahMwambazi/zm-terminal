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

# Separate provider: the Energy Regulation Board Zambia (not BoZ).
ERB_FUEL_POLICY = ProviderPolicy(
    provider_id="erb",
    dataset_id="erb_zambia_fuel_prices",
    name="Energy Regulation Board Zambia",
    source_url="https://www.erb.org.zm/petroleum/",
    classification="public_market_data",
    personal_data_expected=False,
    authentication="none",
    normalized_storage_allowed=True,
    raw_response_storage="disabled",
    attribution_required=True,
    required_provenance=("source", "source_url"),
    required_timestamps=("effective_date", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "Manual job only: ERB's WordPress media API to locate the official \"Pump and Wholesale "
        "Prices\" archive ZIP (https://www.erb.org.zm/wp-json/wp/v2/media), then one download "
        "(about 30 KB, 5 MB cap); 20 s timeout, no retries, no polling, nothing written to disk. "
        "Monthly granularity; the archive lags the latest monthly press release."
    ),
)

MOA_AGRICULTURE_POLICY = ProviderPolicy(
    provider_id="moa",
    dataset_id="moa_national_average_grain_prices",
    name="Ministry of Agriculture, Department of Agribusiness and Marketing",
    source_url="https://www.agriculture.gov.zm/monthly-market-information-bullet/",
    classification="public_market_data",
    personal_data_expected=False,
    authentication="none",
    normalized_storage_allowed=True,
    raw_response_storage="disabled",
    attribution_required=True,
    required_provenance=("source", "source_url", "source_period_label"),
    required_timestamps=("period_start", "period_end", "publication_date", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "Manual import only. Discover official market-bulletin PDFs through the Ministry WordPress media API, "
        "download each candidate once, accept the verified Layout B family only, and fail closed on layout or "
        "cross-table inconsistencies. The Ministry site may require an explicit trusted intermediate CA bundle; "
        "TLS verification must never be disabled."
    ),
)

WORLD_BANK_OIL_POLICY = ProviderPolicy(
    provider_id="world_bank",
    dataset_id="world_bank_pink_sheet_monthly_prices",
    name="World Bank, Commodity Price Data (The Pink Sheet)",
    source_url="https://www.worldbank.org/en/research/commodity-markets",
    classification="public_market_data",
    personal_data_expected=False,
    authentication="none",
    normalized_storage_allowed=True,
    raw_response_storage="disabled",
    attribution_required=True,
    required_provenance=("source", "source_series", "source_url", "unit", "currency"),
    required_timestamps=("period_start", "period_end", "publication_date", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "Manual import only (python -m app.jobs.backfill_oil): one request for the official Commodity Markets page to "
        "find the single monthly-workbook link, one download of CMO-Historical-Data-Monthly.xlsx (about 0.6 MB, 15 MB "
        "cap, Content-Type and OOXML checks, official HTTPS hosts only, redirects validated before they are followed), "
        "30 s timeout, no retries, no polling, nothing written to disk; API calls never reach the World Bank. Only the "
        "Brent and WTI monthly-average columns ($/bbl) are read. Updated monthly (about the 2nd business day); observed "
        "historical values may differ across versions of the workbook, so a changed stored month is a conflict, never "
        "an overwrite. Licence: World Bank Data Catalog dataset 0038238 is classified Public and lists CC BY 4.0 "
        "(https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections, checked "
        "2026-11-14). The Commodity Markets page links the monthly Pink Sheet workbook, but applicability of that catalog "
        "licence to the workbook should be confirmed before public redistribution or commercial deployment: the catalog "
        "metadata also lists an annual periodicity, the workbook carries no licence text and the Pink Sheet PDF footer "
        "reads 'Official Use Only'. Contact: data@worldbank.org. Attribution is required either way."
    ),
)

BITSTAMP_BTC_POLICY = ProviderPolicy(
    provider_id="bitstamp",
    dataset_id="bitstamp_btcusd_spot",
    name="Bitstamp BTC/USD Spot",
    source_url="https://www.bitstamp.net/api/",
    classification="public_market_data",
    personal_data_expected=False,
    authentication="none",
    normalized_storage_allowed=True,
    raw_response_storage="disabled",
    attribution_required=False,
    required_provenance=("source", "market", "source_url"),
    required_timestamps=("observed_at", "candle_open_at", "retrieved_at"),
    redistribution="review_required",
    commercial_use="review_required",
    request_guidance=(
        "Local/internal implementation only. Live ticker (GET /api/v2/ticker/btcusd/): one request per user action, "
        "10 s timeout, no retry, no polling, no tick storage; HTTP 200 is not enough (an unknown market path can return "
        "the all-markets array), so the body must be a single object with last, timestamp and market_type SPOT. "
        "History: manual import only (python -m app.jobs.backfill_btc); GET /api/v2/ohlc/btcusd/ with step=86400, "
        "limit<=1000, exclude_current_candle=true and forward pagination by start + limit (never start with end: end "
        "wins), about 1 s between pages; nothing written to disk. Bitstamp's API page says commercial users should "
        "obtain a Data License Agreement (partners@bitstamp.net); its Terms of Use could not be read during research, "
        "so public display, API redistribution, commercial use and public derived BTC/ZMW stay review_required "
        "(docs/research/btc-provider-discovery.md)."
    ),
)

# Keyed by dataset: one provider may publish several datasets.
PROVIDER_POLICIES: dict[str, ProviderPolicy] = {
    p.dataset_id: p for p in (BOZ_USD_POLICY, BOZ_TBILL_POLICY, BOZ_BOND_POLICY, ERB_FUEL_POLICY, MOA_AGRICULTURE_POLICY, WORLD_BANK_OIL_POLICY, BITSTAMP_BTC_POLICY)
}
