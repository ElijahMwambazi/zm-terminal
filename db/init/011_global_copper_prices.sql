-- Global Copper benchmark: IMF Primary Commodity Prices (PCPS), series G001.PCOPP.USD.M, MONTHLY averages in nominal US
-- dollars per metric ton. Source definition (IMF series metadata): "Copper, grade A cathode, LME spot price, CIF European
-- ports, US$ per metric ton". This is a global benchmark, not a Zambia copper price, not a live quote and not a futures price.
-- A row is one published calendar month (period_start = first day, period_end = last day); no month is invented.
-- Values are stored exactly as published (no quantization, fill or interpolation). A missing month is simply absent.
-- Identity is (provider, benchmark, period). Rows are never updated: a differing value is a conflict (the IMF may revise
-- past months; no revision was observed for Copper, but none is assumed impossible). There is no publication-date column:
-- the SDMX observations carry no trustworthy per-observation release date. Local/internal use: public display, API
-- redistribution and commercial use of IMF Copper data remain review_required (see docs/research/copper-provider-discovery.md).
-- No raw responses are stored. Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/011_global_copper_prices.sql
CREATE TABLE IF NOT EXISTS global_copper_prices (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'imf'),
    benchmark TEXT NOT NULL CHECK (benchmark = 'COPPER'),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    value NUMERIC NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    unit TEXT NOT NULL CHECK (unit = 'metric_ton'),
    price_basis TEXT NOT NULL CHECK (price_basis = 'monthly_average'),
    source_series TEXT NOT NULL,
    source_definition TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    CHECK (EXTRACT(DAY FROM period_start) = 1 AND period_end = (period_start + INTERVAL '1 month' - INTERVAL '1 day')::date),
    UNIQUE (provider, benchmark, period_start, period_end)
);
