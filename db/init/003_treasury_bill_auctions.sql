-- BoZ Treasury Bill auction results: one row per tender + tenor. Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/003_treasury_bill_auctions.sql
CREATE TABLE IF NOT EXISTS treasury_bill_auctions (
    id BIGSERIAL PRIMARY KEY,
    auction_date DATE NOT NULL,
    tender_number TEXT NOT NULL,
    tenor_days INTEGER NOT NULL CHECK (tenor_days IN (91, 182, 273, 364)),
    isin TEXT NOT NULL,
    discount_rate NUMERIC NOT NULL,
    yield_rate NUMERIC NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (auction_date, tender_number, tenor_days)
);
