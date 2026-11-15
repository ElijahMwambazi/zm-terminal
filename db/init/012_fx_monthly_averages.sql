-- Official Bank of Zambia USD/ZMW MONTHLY AVERAGE rates, phase 1: Fortnightly Statistics Table 02 (KWACHA/US DOLLAR EXCHANGE
-- RATES, "Monthly Averages", Bank of Zambia Rates columns), 2014-01 onward, published in rebased kwacha (ZMW per USD).
-- This is a SEPARATE dataset from fx_rates: a row is a calendar month, not an intraday observation, so it has no observed_at.
-- observation_month is the first day of the month (an identity key only; the API exposes it as YYYY-MM).
-- buy / mid / sell are stored exactly as published (NUMERIC, 2 decimals in this source); mid is the BoZ-published mid-rate and is
-- NEVER derived from (buy + sell) / 2. BoZ's monthly-average methodology is not established; no value is repaired or filled.
-- Identity is (base, quote, month, source_series). Rows are never updated: a differing value is a conflict, not a revision.
-- source_publication / source_url / source_page / source_table record the earliest vetted publication that contains the month.
-- Phase 1 needs no ZMK conversion: currency_regime is ZMW. Pre-2013 (old kwacha) values are phase 2 and would add original-value
-- and conversion columns in a later migration. Buy <= mid <= sell is enforced by the importer, not here, so a documented
-- source-published exception can be stored later. No raw PDF bytes or extracted text are stored.
-- Local/internal use: redistribution and commercial use of BoZ data remain review_required. Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/012_fx_monthly_averages.sql
CREATE TABLE IF NOT EXISTS fx_monthly_averages (
    id BIGSERIAL PRIMARY KEY,
    base_currency TEXT NOT NULL CHECK (base_currency = 'USD'),
    quote_currency TEXT NOT NULL CHECK (quote_currency = 'ZMW'),
    observation_month DATE NOT NULL CHECK (EXTRACT(DAY FROM observation_month) = 1),
    buy NUMERIC NOT NULL CHECK (buy > 0),
    mid NUMERIC NOT NULL CHECK (mid > 0),
    sell NUMERIC NOT NULL CHECK (sell > 0),
    source TEXT NOT NULL CHECK (source = 'boz'),
    source_series TEXT NOT NULL,
    source_publication TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_page INTEGER NOT NULL CHECK (source_page > 0),
    source_table TEXT NOT NULL,
    published_precision INTEGER NOT NULL CHECK (published_precision >= 0),
    currency_regime TEXT NOT NULL CHECK (currency_regime = 'ZMW'),
    imported_at TIMESTAMPTZ NOT NULL,
    UNIQUE (base_currency, quote_currency, observation_month, source_series)
);
