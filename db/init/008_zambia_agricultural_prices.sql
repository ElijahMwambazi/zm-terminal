-- Zambia agricultural prices with market semantics kept explicit.
-- The first implemented source is Ministry of Agriculture national-average monthly grain prices.
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/008_zambia_agricultural_prices.sql
CREATE TABLE IF NOT EXISTS zambia_agricultural_prices (
    id BIGSERIAL PRIMARY KEY,
    commodity TEXT NOT NULL CHECK (commodity IN ('MAIZE', 'SOYA_BEANS')),
    market_type TEXT NOT NULL CHECK (market_type IN ('spot', 'national_average', 'procurement', 'government_sale')),
    provider TEXT NOT NULL CHECK (provider IN ('moa', 'fra', 'zamace')),
    observed_date DATE,
    period_start DATE,
    period_end DATE,
    publication_date DATE,
    publication_date_source TEXT,
    season TEXT,
    grade TEXT,
    variety TEXT,
    location_scope TEXT NOT NULL CHECK (location_scope IN ('national', 'province', 'market', 'depot')),
    location TEXT,
    delivery_location TEXT,
    price_basis TEXT NOT NULL CHECK (price_basis IN ('indicative_average', 'reported_trade', 'reference', 'announced_price')),
    source_price NUMERIC NOT NULL CHECK (source_price > 0),
    source_currency TEXT NOT NULL,
    source_unit TEXT NOT NULL,
    normalized_zmw_per_kg NUMERIC CHECK (normalized_zmw_per_kg > 0),
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_period_label TEXT,
    source_record_id TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL,
    CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS zambia_agri_national_average_identity
    ON zambia_agricultural_prices
    (provider, commodity, period_start, period_end, grade, variety, location_scope, location, source_unit)
    NULLS NOT DISTINCT
    WHERE market_type = 'national_average';

CREATE UNIQUE INDEX IF NOT EXISTS zambia_agri_procurement_identity
    ON zambia_agricultural_prices
    (provider, commodity, season, grade, variety, location_scope, location, source_unit)
    NULLS NOT DISTINCT
    WHERE market_type = 'procurement';

CREATE UNIQUE INDEX IF NOT EXISTS zambia_agri_spot_source_identity
    ON zambia_agricultural_prices (provider, source_record_id)
    WHERE market_type = 'spot' AND source_record_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS zambia_agri_history_lookup_idx
    ON zambia_agricultural_prices (commodity, market_type, period_start);
