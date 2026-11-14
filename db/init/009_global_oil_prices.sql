-- Global crude-oil benchmark prices (Brent, WTI): World Bank Commodity Price Data (The Pink Sheet), MONTHLY averages,
-- US dollars per barrel. These are world benchmarks, not Zambia fuel prices, and not daily spot quotes.
-- A row is one calendar month (period_start = first day, period_end = last day); no day is invented for a month.
-- Values are stored exactly as published (no conversion, fill or interpolation). A missing month is simply absent.
-- value has no positivity CHECK: the source contract does not forbid it and a valid published value must not be rejected.
-- Identity is (provider, benchmark, period). Rows are never updated: a differing value is a conflict (the World Bank may
-- revise past months between releases), not a silent revision. publication_date is the "Updated on" date of the workbook
-- release that first supplied the stored value. The UNIQUE constraint's index also serves benchmark/period range reads.
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/009_global_oil_prices.sql
CREATE TABLE IF NOT EXISTS global_oil_prices (
    id BIGSERIAL PRIMARY KEY,
    benchmark TEXT NOT NULL CHECK (benchmark IN ('BRENT', 'WTI')),
    provider TEXT NOT NULL CHECK (provider = 'world_bank'),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    value NUMERIC NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    unit TEXT NOT NULL CHECK (unit = 'barrel'),
    publication_date DATE NOT NULL,
    source_series TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    CHECK (EXTRACT(DAY FROM period_start) = 1 AND period_end = (period_start + INTERVAL '1 month' - INTERVAL '1 day')::date),
    UNIQUE (provider, benchmark, period_start, period_end)
);
