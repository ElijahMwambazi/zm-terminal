-- Global crude-oil benchmark observations (Brent, WTI). These are world benchmarks, not Zambia fuel prices.
-- Source values are stored exactly as published (USD per barrel); no conversion, no fill, no interpolation.
-- value has no positivity CHECK on purpose: WTI settled negative on 2020-04-20 and a valid published value must not be rejected.
-- One row per (benchmark, observed_date, source). Rows are never updated; a differing value is a conflict, not a revision.
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/009_global_oil_benchmarks.sql
CREATE TABLE IF NOT EXISTS global_oil_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    benchmark TEXT NOT NULL CHECK (benchmark IN ('BRENT', 'WTI')),
    observed_date DATE NOT NULL,
    value NUMERIC NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    unit TEXT NOT NULL CHECK (unit = 'barrel'),
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (benchmark, observed_date, source)
);
CREATE INDEX IF NOT EXISTS global_oil_benchmarks_benchmark_date_idx
    ON global_oil_benchmarks (benchmark, observed_date);
