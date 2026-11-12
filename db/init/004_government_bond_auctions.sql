-- BoZ Government Bond auction results: one row per auction + tenor (ISIN is NOT unique:
-- reopened bonds repeat it). Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/004_government_bond_auctions.sql
CREATE TABLE IF NOT EXISTS government_bond_auctions (
    id BIGSERIAL PRIMARY KEY,
    auction_date DATE NOT NULL,
    tender_number TEXT NOT NULL,
    tenor_years INTEGER NOT NULL CHECK (tenor_years IN (2, 3, 5, 7, 10, 15)),
    isin TEXT NOT NULL,
    coupon_rate NUMERIC NOT NULL,
    yield_rate NUMERIC NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (auction_date, tender_number, tenor_years)
);
