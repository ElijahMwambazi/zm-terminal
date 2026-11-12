-- Provenance for persisted FX observations. Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/002_fx_rates_provenance.sql
ALTER TABLE fx_rates ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ;
ALTER TABLE fx_rates ADD COLUMN IF NOT EXISTS source_url TEXT;
