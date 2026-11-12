-- ERB monthly build-up rows (pump and wholesale), one row per product + component, exactly as published.
-- effective_date is the first day of the pricing month (same convention as zambia_fuel_prices).
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/007_zambia_fuel_build_up_items.sql
CREATE TABLE IF NOT EXISTS zambia_fuel_build_up_items (
    id BIGSERIAL PRIMARY KEY,
    effective_date DATE NOT NULL,
    publication_month TEXT NOT NULL,
    build_up_type TEXT NOT NULL CHECK (build_up_type IN ('pump', 'wholesale')),
    product TEXT NOT NULL CHECK (product IN ('PETROL', 'DIESEL', 'KEROSENE', 'JET_A1', 'LSGO')),
    component_code TEXT NOT NULL,
    component_label TEXT NOT NULL,
    position INTEGER NOT NULL,
    amount NUMERIC,            -- NULL = ERB printed "-" (not published), never zero
    currency TEXT,
    unit TEXT NOT NULL DEFAULT '',
    note TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (effective_date, build_up_type, product, component_code, unit)
);
CREATE INDEX IF NOT EXISTS zambia_fuel_build_up_items_lookup_idx
    ON zambia_fuel_build_up_items (product, build_up_type, effective_date);
