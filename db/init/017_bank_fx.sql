-- BoZ commercial bank USD/ZMW daily rates: immutable source-node snapshots and their bank/slot rate rows (storage foundation only).
-- Normative contract: docs/research/boz-commercial-bank-fx.md. Source: the BoZ JSON:API node type commercial_bank_daily_rates.
--   bank_fx_snapshots   one row per encountered source node revision: identity (source_record_id = Drupal node uuid, source_revision_id =
--                       drupal_internal__vid), observation date, source timestamps, the exact raw commercial_bank_rates_date string, layout.
--   bank_fx_rates       one row per (snapshot, slot, institution) for a slot the source PUBLISHED (all four cells present); an unpublished
--                       slot has no row. Exact published strings are stored beside the NUMERIC values and checked against each other.
--   bank_fx_canonical_snapshots  read-only view: one snapshot per observation date (newest node by source_created, then its newest revision).
-- Source-derived data is INSERT-ONLY for every role (row triggers reject UPDATE/DELETE, a statement trigger rejects TRUNCATE): a correction is a
-- new source revision, never an edit. Rights stay review_required (local/internal use only); nothing here means reuse is allowed.
-- Nothing is inserted. SCHEMA BINDING like migration 014: trigger/helper functions read only public.bank_fx_* and pin
-- SET search_path = pg_catalog, pg_temp; they are SECURITY INVOKER. Install in `public`.
-- APPLICATION ROLE: this migration runs AFTER 015 on a clean install, so 015 has not seen these objects. It therefore grants ONLY its own new
-- objects to the already-created application role (SELECT, INSERT on the tables; SELECT on the view; USAGE, SELECT on the sequences; never
-- UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER or ownership) and fails closed if the role is missing or is the admin role. Re-running
-- 015 remains the repository-wide convergence mechanism and applies the same Bank FX policy (015 treats bank_fx_* as append-only).
-- Additive and idempotent; run in a single transaction:
--   docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 --single-transaction' < db/init/017_bank_fx.sql

CREATE TABLE IF NOT EXISTS bank_fx_snapshots (
    id BIGSERIAL PRIMARY KEY,
    observation_date DATE NOT NULL,
    source_record_id UUID NOT NULL,
    source_revision_id BIGINT NOT NULL CHECK (source_revision_id > 0),
    source_internal_nid BIGINT NOT NULL CHECK (source_internal_nid > 0),
    source_created TIMESTAMPTZ NOT NULL,
    source_changed TIMESTAMPTZ NOT NULL,
    source_revision_timestamp TIMESTAMPTZ NOT NULL,
    source_title TEXT NOT NULL CHECK (source_title <> ''),
    source_rates_datetime_raw TEXT NOT NULL CHECK (source_rates_datetime_raw <> ''),
    source_rates_datetime TIMESTAMPTZ NOT NULL,
    table_layout TEXT NOT NULL CHECK (table_layout IN ('v1', 'v2')),
    retrieved_at TIMESTAMPTZ NOT NULL,
    source_url TEXT NOT NULL CHECK (source_url <> ''),
    rights_status TEXT NOT NULL CHECK (rights_status = 'review_required'),
    CONSTRAINT bank_fx_snapshots_node_revision_key UNIQUE (source_record_id, source_revision_id)
);
CREATE INDEX IF NOT EXISTS bank_fx_snapshots_date_idx ON bank_fx_snapshots (observation_date DESC, source_created DESC);

-- Exact text <-> NUMERIC agreement. The CASE guarantees the text grammar is known valid before the text is cast (boolean AND/OR order is
-- unspecified), so malformed text yields false instead of a cast error. NaN and the infinities can never equal a finite parsed value.
CREATE OR REPLACE FUNCTION bank_fx_rate_matches(rate_text TEXT, rate_value NUMERIC) RETURNS BOOLEAN AS $$
    SELECT CASE WHEN rate_text ~ '^[0-9]+[.][0-9]{1,4}$' THEN rate_value = rate_text::numeric AND rate_text::numeric > 0 ELSE false END
$$ LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp;

CREATE TABLE IF NOT EXISTS bank_fx_rates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES bank_fx_snapshots (id),
    source_row_index INTEGER NOT NULL CHECK (source_row_index >= 3),  -- CMS table-row key; rows 0-2 are the header rows
    slot TEXT NOT NULL CHECK (slot IN ('09:30', '12:30', '15:30')),
    institution_name_raw TEXT NOT NULL CHECK (institution_name_raw <> ''),
    institution_name TEXT NOT NULL CHECK (institution_name <> '' AND institution_name = btrim(institution_name)),
    is_market_average BOOLEAN NOT NULL,
    interbank_buy NUMERIC NOT NULL,
    interbank_buy_text TEXT NOT NULL,
    interbank_sell NUMERIC NOT NULL,
    interbank_sell_text TEXT NOT NULL,
    retail_buy NUMERIC NOT NULL,
    retail_buy_text TEXT NOT NULL,
    retail_sell NUMERIC NOT NULL,
    retail_sell_text TEXT NOT NULL,
    retail_source_label TEXT NOT NULL CHECK (retail_source_label IN ('Retail Rates', 'BOARD RATES')),
    CONSTRAINT bank_fx_rates_identity_key UNIQUE (snapshot_id, slot, institution_name),
    CONSTRAINT bank_fx_rates_row_key UNIQUE (snapshot_id, slot, source_row_index),
    CONSTRAINT bank_fx_rates_market_average_name CHECK (is_market_average = (lower(institution_name) = 'market average')),
    CONSTRAINT bank_fx_rates_interbank_buy_text CHECK (bank_fx_rate_matches(interbank_buy_text, interbank_buy)),
    CONSTRAINT bank_fx_rates_interbank_sell_text CHECK (bank_fx_rate_matches(interbank_sell_text, interbank_sell)),
    CONSTRAINT bank_fx_rates_retail_buy_text CHECK (bank_fx_rate_matches(retail_buy_text, retail_buy)),
    CONSTRAINT bank_fx_rates_retail_sell_text CHECK (bank_fx_rate_matches(retail_sell_text, retail_sell))
);
-- At most one source market-average row per snapshot and slot (the parser additionally requires exactly one per node).
CREATE UNIQUE INDEX IF NOT EXISTS bank_fx_rates_one_market_average ON bank_fx_rates (snapshot_id, slot) WHERE is_market_average;
CREATE INDEX IF NOT EXISTS bank_fx_rates_snapshot_idx ON bank_fx_rates (snapshot_id, slot, source_row_index);

-- ------------------------------------------------------------------------------------------------ insert-only (every role)
CREATE OR REPLACE FUNCTION bank_fx_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: Bank FX source data is insert-only (a correction is a new source revision)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;

DROP TRIGGER IF EXISTS bank_fx_snapshots_immutable ON bank_fx_snapshots;
CREATE TRIGGER bank_fx_snapshots_immutable BEFORE UPDATE OR DELETE ON bank_fx_snapshots
    FOR EACH ROW EXECUTE FUNCTION bank_fx_reject_change();
DROP TRIGGER IF EXISTS bank_fx_rates_immutable ON bank_fx_rates;
CREATE TRIGGER bank_fx_rates_immutable BEFORE UPDATE OR DELETE ON bank_fx_rates
    FOR EACH ROW EXECUTE FUNCTION bank_fx_reject_change();
DROP TRIGGER IF EXISTS bank_fx_snapshots_no_truncate ON bank_fx_snapshots;
CREATE TRIGGER bank_fx_snapshots_no_truncate BEFORE TRUNCATE ON bank_fx_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION bank_fx_reject_change();
DROP TRIGGER IF EXISTS bank_fx_rates_no_truncate ON bank_fx_rates;
CREATE TRIGGER bank_fx_rates_no_truncate BEFORE TRUNCATE ON bank_fx_rates
    FOR EACH STATEMENT EXECUTE FUNCTION bank_fx_reject_change();

-- The retail source label must match the parent snapshot's layout: v1 -> 'Retail Rates', v2 -> 'BOARD RATES'.
CREATE OR REPLACE FUNCTION bank_fx_check_label() RETURNS trigger AS $$
DECLARE
    layout TEXT;
    expected TEXT;
BEGIN
    SELECT s.table_layout INTO layout FROM public.bank_fx_snapshots s WHERE s.id = NEW.snapshot_id;
    expected := CASE layout WHEN 'v1' THEN 'Retail Rates' WHEN 'v2' THEN 'BOARD RATES' END;
    IF expected IS NULL OR NEW.retail_source_label IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'bank_fx_rates: retail_source_label % does not match the snapshot layout %', NEW.retail_source_label, layout
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
DROP TRIGGER IF EXISTS bank_fx_rates_label ON bank_fx_rates;
CREATE TRIGGER bank_fx_rates_label BEFORE INSERT ON bank_fx_rates
    FOR EACH ROW EXECUTE FUNCTION bank_fx_check_label();

-- ----------------------------------------------------------------------------------------------------- canonical snapshot per date
-- Two stages, never merged: (1) per observation date the NODE with the newest source_created (ties: larger source_internal_nid, then
-- source_record_id); (2) of that node's revisions stored for that date, the newest source_revision_timestamp (ties: larger
-- source_revision_id). One snapshot per observation date; rates of other snapshots are never combined. Not a "latest overall" value.
CREATE OR REPLACE VIEW bank_fx_canonical_snapshots AS
WITH winning_node AS (
    SELECT DISTINCT ON (observation_date) observation_date, source_record_id
    FROM bank_fx_snapshots
    ORDER BY observation_date, source_created DESC, source_internal_nid DESC, source_record_id DESC
)
SELECT DISTINCT ON (s.observation_date)
       s.id AS snapshot_id, s.observation_date, s.source_record_id, s.source_revision_id, s.source_internal_nid,
       s.source_created, s.source_changed, s.source_revision_timestamp, s.source_title,
       s.source_rates_datetime_raw, s.source_rates_datetime, s.table_layout, s.retrieved_at, s.source_url, s.rights_status
FROM bank_fx_snapshots s
JOIN winning_node w ON w.observation_date = s.observation_date AND w.source_record_id = s.source_record_id
ORDER BY s.observation_date, s.source_revision_timestamp DESC, s.source_revision_id DESC;

-- ------------------------------------------------------------------------------------ application-role grants (own objects only)
DO $$
DECLARE
    app_role text := COALESCE(NULLIF(current_setting('zm.app_role', true), ''), 'zm_terminal_app');
    sch text := current_schema();
    rel text;
BEGIN
    IF app_role = current_user THEN
        RAISE EXCEPTION 'the application role must differ from the admin role running this script (%)', app_role;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        RAISE EXCEPTION 'application role % does not exist: apply db/init/015_application_role.sql first', app_role;
    END IF;
    FOREACH rel IN ARRAY ARRAY['bank_fx_snapshots', 'bank_fx_rates'] LOOP
        EXECUTE format('REVOKE ALL ON %I.%I FROM %I', sch, rel, app_role);
        EXECUTE format('GRANT SELECT, INSERT ON %I.%I TO %I', sch, rel, app_role);
    END LOOP;
    EXECUTE format('REVOKE ALL ON %I.bank_fx_canonical_snapshots FROM %I', sch, app_role);
    EXECUTE format('GRANT SELECT ON %I.bank_fx_canonical_snapshots TO %I', sch, app_role);
    FOREACH rel IN ARRAY ARRAY['bank_fx_snapshots_id_seq', 'bank_fx_rates_id_seq'] LOOP
        EXECUTE format('REVOKE ALL ON SEQUENCE %I.%I FROM %I', sch, rel, app_role);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.%I TO %I', sch, rel, app_role);
    END LOOP;
END
$$;
