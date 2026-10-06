CREATE TABLE IF NOT EXISTS instruments (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    exchange TEXT,
    country TEXT,
    provider TEXT,
    provider_symbol TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_prices (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC NOT NULL,
    volume NUMERIC,
    source TEXT NOT NULL,
    UNIQUE (instrument_id, observed_at, source)
);

CREATE INDEX IF NOT EXISTS idx_market_prices_instrument_time
    ON market_prices (instrument_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS fx_rates (
    id BIGSERIAL PRIMARY KEY,
    base_currency TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    buy NUMERIC,
    mid NUMERIC,
    sell NUMERIC,
    source TEXT NOT NULL,
    UNIQUE (base_currency, quote_currency, observed_at, source)
);

CREATE TABLE IF NOT EXISTS macro_observations (
    id BIGSERIAL PRIMARY KEY,
    series_id TEXT NOT NULL,
    country TEXT NOT NULL,
    observed_on DATE NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT,
    source TEXT NOT NULL,
    UNIQUE (series_id, country, observed_on, source)
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument_id)
);

INSERT INTO instruments (symbol, name, asset_class, currency, exchange, country, provider, provider_symbol)
VALUES
    ('USDZMW', 'US Dollar / Zambian Kwacha', 'fx', 'ZMW', NULL, 'ZM', 'boz', 'USDZMW'),
    ('BTCUSD', 'Bitcoin / US Dollar', 'crypto', 'USD', NULL, NULL, 'openbb', 'BTCUSD'),
    ('BTCZMW', 'Bitcoin / Zambian Kwacha', 'crypto', 'ZMW', NULL, 'ZM', 'derived', 'BTCZMW'),
    ('LASI', 'LuSE All Share Index', 'index', 'ZMW', 'LuSE', 'ZM', 'luse', 'LASI'),
    ('CECZ', 'Copperbelt Energy Corporation', 'equity', 'ZMW', 'LuSE', 'ZM', 'luse', 'CECZ'),
    ('ZNCO', 'Zanaco', 'equity', 'ZMW', 'LuSE', 'ZM', 'luse', 'ZNCO'),
    ('COPPER', 'Copper', 'commodity', 'USD', NULL, NULL, 'openbb', 'COPPER')
ON CONFLICT (symbol) DO NOTHING;
