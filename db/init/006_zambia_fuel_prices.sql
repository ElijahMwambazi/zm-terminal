-- ERB Zambia fuel prices (monthly; effective_date is the first day of the pricing month).
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/006_zambia_fuel_prices.sql
CREATE TABLE IF NOT EXISTS zambia_fuel_prices (
    id BIGSERIAL PRIMARY KEY,
    effective_date DATE NOT NULL,
    product TEXT NOT NULL CHECK (product IN ('PETROL', 'DIESEL', 'KEROSENE', 'JET_A1', 'LSGO')),
    price_type TEXT NOT NULL CHECK (price_type IN ('retail', 'wholesale')),
    price_zmw NUMERIC NOT NULL CHECK (price_zmw > 0),
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (effective_date, product, price_type, unit)
);
CREATE INDEX IF NOT EXISTS zambia_fuel_prices_product_type_date_idx
    ON zambia_fuel_prices (product, price_type, effective_date);
