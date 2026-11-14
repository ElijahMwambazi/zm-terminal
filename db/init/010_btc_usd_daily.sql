-- Completed daily BTC/USD OHLCV candles from the Bitstamp public API (market btcusd, SPOT), normalized values only.
-- A row is one completed UTC-day candle (step=86400); candle_open_at is the candle's UTC 00:00 open. Volume is BTC.
-- Values are stored exactly as published (no quantization, fill or interpolation). Zero-volume candles that Bitstamp
-- publishes are stored as published (observed source behaviour; generation methodology not documented): they are not
-- deleted, converted to missing observations or forward-filled by us. A day Bitstamp does not return stays absent.
-- Identity is (provider, market, interval, candle_open_at). Rows are never updated: a differing candle is a conflict
-- (no silent revision). Local/internal use: public redistribution and commercial use of Bitstamp data remain
-- review_required (see docs/research/btc-provider-discovery.md). No raw responses and no live ticks are stored.
-- Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/010_btc_usd_daily.sql
CREATE TABLE IF NOT EXISTS btc_usd_daily (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'bitstamp'),
    market TEXT NOT NULL CHECK (market = 'BTC/USD'),
    interval TEXT NOT NULL CHECK (interval = '1d'),
    candle_open_at TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL CHECK (open > 0),
    high NUMERIC NOT NULL CHECK (high > 0),
    low NUMERIC NOT NULL CHECK (low > 0),
    close NUMERIC NOT NULL CHECK (close > 0),
    volume_btc NUMERIC NOT NULL CHECK (volume_btc >= 0),
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    CHECK (MOD(EXTRACT(EPOCH FROM candle_open_at), 86400) = 0),
    CHECK (low <= open AND open <= high AND low <= close AND close <= high),
    UNIQUE (provider, market, interval, candle_open_at)
);
