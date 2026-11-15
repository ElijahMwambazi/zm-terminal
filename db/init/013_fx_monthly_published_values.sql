-- Phase 2 foundation for fx_monthly_averages: keep the value BoZ actually PRINTED next to the canonical ZMW value.
-- buy / mid / sell stay the CANONICAL ZMW-per-USD values (what the API serves). The new columns record the original
-- publication representation and the exact, deterministic normalization:
--   published_buy / published_mid / published_sell  the printed values, exactly (NUMERIC, no rounding)
--   published_currency  'ZMK' = old kwacha (observations before 2013-01), 'ZMW' = rebased kwacha (2013-01 onward)
--   normalization_divisor  1 (already rebased) or 1000 (old kwacha / 1,000, BoZ rebasing effective 2013-01-01)
-- Invariant enforced here for all three rates by exact NUMERIC arithmetic:  canonical * divisor = published
-- (so canonical = published / divisor, with no floating point and no rounding). The regime is fixed by the OBSERVATION
-- MONTH, never by value magnitude: before 2013-01-01 -> ZMK and 1000; from 2013-01-01 -> ZMW and 1.
-- Existing phase-1 rows (2014 onward, rebased) are migrated in place: published = current canonical values, ZMW, divisor 1.
-- No canonical rate, observation month, provenance, source series or imported_at is changed. Additive and idempotent (run it in a single transaction so a failure changes nothing):
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal -v ON_ERROR_STOP=1 --single-transaction < db/init/013_fx_monthly_published_values.sql
-- Requires 012_fx_monthly_averages.sql. There is deliberately no buy <= mid <= sell constraint here (source-published ordering
-- exceptions exist and are handled narrowly by the importer). Local/internal use only; rights remain review_required.
ALTER TABLE fx_monthly_averages ADD COLUMN IF NOT EXISTS published_buy NUMERIC;
ALTER TABLE fx_monthly_averages ADD COLUMN IF NOT EXISTS published_mid NUMERIC;
ALTER TABLE fx_monthly_averages ADD COLUMN IF NOT EXISTS published_sell NUMERIC;
ALTER TABLE fx_monthly_averages ADD COLUMN IF NOT EXISTS published_currency TEXT;
ALTER TABLE fx_monthly_averages ADD COLUMN IF NOT EXISTS normalization_divisor NUMERIC;

-- Existing rows are rebased ZMW (2014 onward): printed values equal the canonical values and the divisor is exactly 1.
-- The WHERE guard refuses to guess for any pre-2013 row that might exist without published values.
UPDATE fx_monthly_averages
SET published_buy = buy, published_mid = mid, published_sell = sell, published_currency = 'ZMW', normalization_divisor = 1
WHERE published_buy IS NULL AND published_mid IS NULL AND published_sell IS NULL
  AND published_currency IS NULL AND normalization_divisor IS NULL
  AND observation_month >= DATE '2013-01-01';

ALTER TABLE fx_monthly_averages ALTER COLUMN published_buy SET NOT NULL;
ALTER TABLE fx_monthly_averages ALTER COLUMN published_mid SET NOT NULL;
ALTER TABLE fx_monthly_averages ALTER COLUMN published_sell SET NOT NULL;
ALTER TABLE fx_monthly_averages ALTER COLUMN published_currency SET NOT NULL;
ALTER TABLE fx_monthly_averages ALTER COLUMN normalization_divisor SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fx_monthly_published_values_finite_positive'
                   AND conrelid = 'fx_monthly_averages'::regclass) THEN
        ALTER TABLE fx_monthly_averages ADD CONSTRAINT fx_monthly_published_values_finite_positive CHECK (
            published_buy > 0 AND published_buy NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC)
            AND published_mid > 0 AND published_mid NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC)
            AND published_sell > 0 AND published_sell NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC)
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fx_monthly_normalization_regime'
                   AND conrelid = 'fx_monthly_averages'::regclass) THEN
        -- Regime by observation month only: old kwacha before the 2013-01-01 rebasing, rebased kwacha from it.
        ALTER TABLE fx_monthly_averages ADD CONSTRAINT fx_monthly_normalization_regime CHECK (
            (observation_month < DATE '2013-01-01' AND published_currency = 'ZMK' AND normalization_divisor = 1000)
            OR (observation_month >= DATE '2013-01-01' AND published_currency = 'ZMW' AND normalization_divisor = 1)
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fx_monthly_canonical_equals_published_over_divisor'
                   AND conrelid = 'fx_monthly_averages'::regclass) THEN
        -- canonical = published / divisor, as exact NUMERIC equality (multiplication is exact; scale is irrelevant).
        ALTER TABLE fx_monthly_averages ADD CONSTRAINT fx_monthly_canonical_equals_published_over_divisor CHECK (
            buy * normalization_divisor = published_buy
            AND mid * normalization_divisor = published_mid
            AND sell * normalization_divisor = published_sell
        );
    END IF;
END $$;

