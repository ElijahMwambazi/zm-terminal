-- Energy annual generation storage: source-candidate review, accepted ERB publication revisions, local import revisions, and
-- immutable station/observation/group-total data. Normative design: docs/design/energy-generation-storage-vintages.md.
-- Three independent lifecycles (never one state machine):
--   energy_publication_candidates  source files under review      pending | rejected | accepted      (no revision number, no data)
--   energy_publications            accepted ERB publication revisions  approved | superseded         (official vintage; revision_sequence)
--   energy_generation_imports      successful ZM Terminal imports of one publication  current | superseded  (import_sequence)
-- Observations and group totals belong to an IMPORT; publication provenance is reached through observation -> import -> publication.
-- The database is authoritative for every lifecycle invariant PostgreSQL can express. Deferred constraint triggers evaluate the
-- exactly-one-current-import, chain-consistency, candidate-link and child-count rules at COMMIT, so the atomic first-import,
-- same-year replacement (r1 -> r2) and local import correction (import 1 -> import 2) transactions are possible. Exact manifest key
-- sets, parser diagnostics and identical-versus-conflicting dataset comparison remain future application responsibilities.
-- Stored values are exact NUMERIC with the printed scale kept in published_precision (393.40 and 393.4 stay distinguishable).
-- No data is inserted. Rights for all ERB data remain review_required (local/internal use only): no status here means reuse is allowed.
-- Additive and idempotent; run in a single transaction so a failure changes nothing:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal -v ON_ERROR_STOP=1 --single-transaction < db/init/014_energy_generation_vintages.sql

-- ---------------------------------------------------------------------------------------------------------------- publications
CREATE TABLE IF NOT EXISTS energy_publications (
    id BIGSERIAL PRIMARY KEY,
    publisher TEXT NOT NULL CHECK (publisher = 'ERB'),
    source_series TEXT NOT NULL CHECK (source_series = btrim(source_series) AND source_series <> ''),
    publication_year SMALLINT NOT NULL CHECK (publication_year BETWEEN 2000 AND 2100),
    revision_sequence INTEGER NOT NULL CHECK (revision_sequence >= 1),
    publication_status TEXT NOT NULL CHECK (publication_status IN ('approved', 'superseded')),
    supersedes_publication_id BIGINT,
    title TEXT NOT NULL CHECK (btrim(title) <> ''),
    cover_date TEXT,
    source_url TEXT NOT NULL CHECK (btrim(source_url) <> ''),
    source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_size_bytes BIGINT NOT NULL CHECK (source_size_bytes > 0),
    pdf_pages INTEGER NOT NULL CHECK (pdf_pages > 0),
    rights_status TEXT NOT NULL CHECK (rights_status = 'review_required'),
    first_retrieved_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_note TEXT,
    CONSTRAINT energy_publications_identity UNIQUE (source_series, publication_year, revision_sequence),
    CONSTRAINT energy_publications_source_sha256_key UNIQUE (source_sha256),
    CONSTRAINT energy_publications_one_successor UNIQUE (supersedes_publication_id),
    CONSTRAINT energy_publications_scope UNIQUE (id, source_series, publication_year),
    -- A replacement belongs to the same series AND the same nominal year as the revision it replaces (skipped while NULL).
    CONSTRAINT energy_publications_supersedes_same_scope FOREIGN KEY (supersedes_publication_id, source_series, publication_year)
        REFERENCES energy_publications (id, source_series, publication_year),
    CONSTRAINT energy_publications_r1_has_no_predecessor CHECK ((revision_sequence = 1) = (supersedes_publication_id IS NULL)),
    CONSTRAINT energy_publications_not_self CHECK (supersedes_publication_id IS DISTINCT FROM id)
);

-- ------------------------------------------------------------------------------------------------------------------ candidates
CREATE TABLE IF NOT EXISTS energy_publication_candidates (
    id BIGSERIAL PRIMARY KEY,
    source_series TEXT NOT NULL CHECK (source_series = btrim(source_series) AND source_series <> ''),
    nominal_publication_year SMALLINT NOT NULL CHECK (nominal_publication_year BETWEEN 2000 AND 2100),
    source_url TEXT NOT NULL CHECK (btrim(source_url) <> ''),
    source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_size_bytes BIGINT NOT NULL CHECK (source_size_bytes > 0),
    discovered_at TIMESTAMPTZ NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'rejected', 'accepted')),
    review_note TEXT,
    reviewed_at TIMESTAMPTZ,
    accepted_publication_id BIGINT REFERENCES energy_publications (id),
    CONSTRAINT energy_publication_candidates_sha256_key UNIQUE (source_sha256),
    CONSTRAINT energy_publication_candidates_one_publication UNIQUE (accepted_publication_id),
    CONSTRAINT energy_publication_candidates_accepted_link CHECK ((review_status = 'accepted') = (accepted_publication_id IS NOT NULL)),
    CONSTRAINT energy_publication_candidates_reviewed CHECK (
        review_status = 'pending' OR (review_note IS NOT NULL AND btrim(review_note) <> '' AND reviewed_at IS NOT NULL)),
    CONSTRAINT energy_publication_candidates_pending_blank CHECK (
        review_status <> 'pending' OR (review_note IS NULL AND reviewed_at IS NULL))
);

-- ---------------------------------------------------------------------------------------------------------------------- imports
CREATE TABLE IF NOT EXISTS energy_generation_imports (
    id BIGSERIAL PRIMARY KEY,
    publication_id BIGINT NOT NULL REFERENCES energy_publications (id),
    import_sequence INTEGER NOT NULL CHECK (import_sequence >= 1),
    import_status TEXT NOT NULL CHECK (import_status IN ('current', 'superseded')),
    expected_observation_count INTEGER NOT NULL CHECK (expected_observation_count >= 0),
    expected_group_total_count INTEGER NOT NULL CHECK (expected_group_total_count >= 0),
    importer_version TEXT NOT NULL CHECK (btrim(importer_version) <> ''),
    correction_reason TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_note TEXT,
    CONSTRAINT energy_generation_imports_identity UNIQUE (publication_id, import_sequence),
    CONSTRAINT energy_generation_imports_correction_reason CHECK (
        (import_sequence = 1) = (correction_reason IS NULL) AND (correction_reason IS NULL OR btrim(correction_reason) <> ''))
);
-- Deliberately no immediate partial UNIQUE index on (publication_id) WHERE import_status = 'current': it would forbid inserting
-- the corrected import before demoting the old one. "At most one" and "at least one" are enforced at commit by deferred triggers.

-- --------------------------------------------------------------------------------------------------------------------- stations
CREATE TABLE IF NOT EXISTS energy_source_stations (
    id BIGSERIAL PRIMARY KEY,
    source_series TEXT NOT NULL CHECK (source_series = btrim(source_series) AND source_series <> ''),
    station_group TEXT NOT NULL CHECK (station_group IN
        ('zesco_large_hydro', 'zesco_mini_hydro', 'zesco_diesel', 'independent_power_producer')),
    station_name TEXT NOT NULL CHECK (station_name = btrim(station_name) AND station_name <> ''),
    CONSTRAINT energy_source_stations_identity UNIQUE (source_series, station_group, station_name)
);

-- ----------------------------------------------------------------------------------------------------------------- observations
CREATE TABLE IF NOT EXISTS energy_generation_observations (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT NOT NULL REFERENCES energy_generation_imports (id),
    source_station_id BIGINT NOT NULL REFERENCES energy_source_stations (id),
    observation_year SMALLINT NOT NULL CHECK (observation_year BETWEEN 1990 AND 2100),
    value_state TEXT NOT NULL CHECK (value_state IN ('reported', 'not_reported')),
    generation_gwh NUMERIC,
    published_precision INTEGER,
    source_table TEXT NOT NULL CHECK (btrim(source_table) <> ''),
    source_pdf_page INTEGER NOT NULL CHECK (source_pdf_page > 0),
    source_printed_page INTEGER NOT NULL CHECK (source_printed_page > 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT energy_generation_observations_identity UNIQUE (import_id, source_station_id, observation_year),
    CONSTRAINT energy_generation_observations_state_value CHECK (
        (value_state = 'reported' AND generation_gwh IS NOT NULL AND published_precision IS NOT NULL
            AND published_precision >= 0
            AND generation_gwh NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC)
            AND generation_gwh >= 0 AND generation_gwh < 1000000
            AND scale(generation_gwh) = published_precision)
        OR (value_state = 'not_reported' AND generation_gwh IS NULL AND published_precision IS NULL))
);
CREATE INDEX IF NOT EXISTS energy_generation_observations_station_year
    ON energy_generation_observations (source_station_id, observation_year, import_id);

-- ------------------------------------------------------------------------------------------------------------------ group totals
CREATE TABLE IF NOT EXISTS energy_generation_group_totals (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT NOT NULL REFERENCES energy_generation_imports (id),
    station_group TEXT NOT NULL CHECK (station_group IN
        ('zesco_large_hydro', 'zesco_mini_hydro', 'zesco_diesel', 'independent_power_producer')),
    observation_year SMALLINT NOT NULL CHECK (observation_year BETWEEN 1990 AND 2100),
    total_gwh NUMERIC NOT NULL CHECK (total_gwh NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC)
        AND total_gwh >= 0 AND total_gwh < 1000000),
    published_precision INTEGER NOT NULL CHECK (published_precision >= 0),
    source_table TEXT NOT NULL CHECK (btrim(source_table) <> ''),
    source_pdf_page INTEGER NOT NULL CHECK (source_pdf_page > 0),
    source_printed_page INTEGER NOT NULL CHECK (source_printed_page > 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT energy_generation_group_totals_identity UNIQUE (import_id, station_group, observation_year),
    CONSTRAINT energy_generation_group_totals_scale CHECK (scale(total_gwh) = published_precision)
);

-- =================================================================== immutability and lifecycle-transition triggers (BEFORE)
CREATE OR REPLACE FUNCTION energy_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: Energy source-derived data is insert-only', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION energy_reject_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DELETE on % is not allowed: historical Energy records are never deleted', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

-- Candidates: identity columns are immutable; review fields move only along pending -> rejected|accepted, rejected -> accepted.
CREATE OR REPLACE FUNCTION energy_candidates_before_update() RETURNS trigger AS $$
BEGIN
    IF (NEW.id, NEW.source_series, NEW.nominal_publication_year, NEW.source_url, NEW.source_sha256, NEW.source_size_bytes, NEW.discovered_at)
       IS DISTINCT FROM (OLD.id, OLD.source_series, OLD.nominal_publication_year, OLD.source_url, OLD.source_sha256, OLD.source_size_bytes, OLD.discovered_at) THEN
        RAISE EXCEPTION 'energy_publication_candidates: source identity columns are immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.review_status = OLD.review_status THEN
        IF (NEW.review_note, NEW.reviewed_at, NEW.accepted_publication_id) IS DISTINCT FROM (OLD.review_note, OLD.reviewed_at, OLD.accepted_publication_id) THEN
            RAISE EXCEPTION 'energy_publication_candidates: review fields change only with a status transition' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NOT ((OLD.review_status = 'pending' AND NEW.review_status IN ('rejected', 'accepted'))
               OR (OLD.review_status = 'rejected' AND NEW.review_status = 'accepted')) THEN
        RAISE EXCEPTION 'energy_publication_candidates: illegal transition % -> %', OLD.review_status, NEW.review_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Publications: consecutive same-scope predecessor on insert; only approved -> superseded afterwards.
CREATE OR REPLACE FUNCTION energy_publications_before_insert() RETURNS trigger AS $$
DECLARE pred_revision INTEGER;
BEGIN
    IF NEW.revision_sequence > 1 THEN
        SELECT revision_sequence INTO pred_revision FROM energy_publications
        WHERE id = NEW.supersedes_publication_id AND source_series = NEW.source_series AND publication_year = NEW.publication_year;
        IF pred_revision IS NULL OR pred_revision <> NEW.revision_sequence - 1 THEN
            RAISE EXCEPTION 'energy_publications: revision % must supersede revision % of the same series and publication year',
                NEW.revision_sequence, NEW.revision_sequence - 1 USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION energy_publications_before_update() RETURNS trigger AS $$
BEGIN
    IF (NEW.id, NEW.publisher, NEW.source_series, NEW.publication_year, NEW.revision_sequence, NEW.supersedes_publication_id, NEW.title,
        NEW.cover_date, NEW.source_url, NEW.source_sha256, NEW.source_size_bytes, NEW.pdf_pages, NEW.rights_status,
        NEW.first_retrieved_at, NEW.accepted_at)
       IS DISTINCT FROM
       (OLD.id, OLD.publisher, OLD.source_series, OLD.publication_year, OLD.revision_sequence, OLD.supersedes_publication_id, OLD.title,
        OLD.cover_date, OLD.source_url, OLD.source_sha256, OLD.source_size_bytes, OLD.pdf_pages, OLD.rights_status,
        OLD.first_retrieved_at, OLD.accepted_at) THEN
        RAISE EXCEPTION 'energy_publications: source-derived columns are immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.publication_status = OLD.publication_status THEN
        IF (NEW.status_changed_at, NEW.status_note) IS DISTINCT FROM (OLD.status_changed_at, OLD.status_note) THEN
            RAISE EXCEPTION 'energy_publications: status fields change only with a status transition' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NOT (OLD.publication_status = 'approved' AND NEW.publication_status = 'superseded') THEN
        RAISE EXCEPTION 'energy_publications: illegal transition % -> %', OLD.publication_status, NEW.publication_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Imports: consecutive import_sequence per publication; only current -> superseded afterwards.
CREATE OR REPLACE FUNCTION energy_imports_before_insert() RETURNS trigger AS $$
DECLARE previous INTEGER;
BEGIN
    SELECT COALESCE(MAX(import_sequence), 0) INTO previous FROM energy_generation_imports WHERE publication_id = NEW.publication_id;
    IF NEW.import_sequence <> previous + 1 THEN
        RAISE EXCEPTION 'energy_generation_imports: import_sequence must be % for this publication, got %', previous + 1, NEW.import_sequence
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION energy_imports_before_update() RETURNS trigger AS $$
BEGIN
    IF (NEW.id, NEW.publication_id, NEW.import_sequence, NEW.expected_observation_count, NEW.expected_group_total_count,
        NEW.importer_version, NEW.correction_reason, NEW.imported_at)
       IS DISTINCT FROM
       (OLD.id, OLD.publication_id, OLD.import_sequence, OLD.expected_observation_count, OLD.expected_group_total_count,
        OLD.importer_version, OLD.correction_reason, OLD.imported_at) THEN
        RAISE EXCEPTION 'energy_generation_imports: stable columns are immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.import_status = OLD.import_status THEN
        IF (NEW.status_changed_at, NEW.status_note) IS DISTINCT FROM (OLD.status_changed_at, OLD.status_note) THEN
            RAISE EXCEPTION 'energy_generation_imports: status fields change only with a status transition' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NOT (OLD.import_status = 'current' AND NEW.import_status = 'superseded') THEN
        RAISE EXCEPTION 'energy_generation_imports: illegal transition % -> %', OLD.import_status, NEW.import_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Observations may only reference a station of the publication's own source series.
CREATE OR REPLACE FUNCTION energy_observations_before_insert() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM energy_generation_imports i
        JOIN energy_publications p ON p.id = i.publication_id
        JOIN energy_source_stations s ON s.id = NEW.source_station_id AND s.source_series = p.source_series
        WHERE i.id = NEW.import_id) THEN
        RAISE EXCEPTION 'energy_generation_observations: station belongs to a different source series than the import''s publication'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================== commit-time (deferred constraint trigger) checks
-- Exactly one current import per publication, evaluated at COMMIT.
CREATE OR REPLACE FUNCTION energy_check_one_current_import() RETURNS trigger AS $$
DECLARE pid BIGINT; n INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'energy_publications' THEN pid := NEW.id; ELSE pid := NEW.publication_id; END IF;
    SELECT COUNT(*) INTO n FROM energy_generation_imports WHERE publication_id = pid AND import_status = 'current';
    IF n <> 1 THEN
        RAISE EXCEPTION 'energy publication % must have exactly one current import at commit, found %', pid, n
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Actual child cardinalities equal the import's stored expectations at COMMIT (no global 390/52: counts are per import).
CREATE OR REPLACE FUNCTION energy_check_import_counts() RETURNS trigger AS $$
DECLARE iid BIGINT; exp_obs INTEGER; exp_tot INTEGER; act_obs INTEGER; act_tot INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'energy_generation_imports' THEN iid := NEW.id; ELSE iid := NEW.import_id; END IF;
    SELECT expected_observation_count, expected_group_total_count INTO exp_obs, exp_tot FROM energy_generation_imports WHERE id = iid;
    SELECT COUNT(*) INTO act_obs FROM energy_generation_observations WHERE import_id = iid;
    SELECT COUNT(*) INTO act_tot FROM energy_generation_group_totals WHERE import_id = iid;
    IF act_obs <> exp_obs OR act_tot <> exp_tot THEN
        RAISE EXCEPTION 'energy import % has % observations / % group totals at commit, expected % / %', iid, act_obs, act_tot, exp_obs, exp_tot
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Per (source_series, publication_year): revisions are 1..max with no gaps, the highest is approved and all lower ones are
-- superseded. Different publication years are independent (2023 r1, 2024 r1 and 2025 r1 are all approved together).
-- Every accepted publication must also be linked from exactly one accepted candidate with the same series, year, URL, SHA and size.
CREATE OR REPLACE FUNCTION energy_check_publication_chain() RETURNS trigger AS $$
DECLARE max_rev INTEGER; n_rev INTEGER; top_status TEXT; n_approved INTEGER;
BEGIN
    SELECT MAX(revision_sequence), COUNT(*), COUNT(*) FILTER (WHERE publication_status = 'approved')
      INTO max_rev, n_rev, n_approved
      FROM energy_publications WHERE source_series = NEW.source_series AND publication_year = NEW.publication_year;
    SELECT publication_status INTO top_status FROM energy_publications
      WHERE source_series = NEW.source_series AND publication_year = NEW.publication_year AND revision_sequence = max_rev;
    IF n_rev <> max_rev OR top_status <> 'approved' OR n_approved <> 1 THEN
        RAISE EXCEPTION 'energy publication chain for % / % is inconsistent at commit: the highest revision (%) must be the only approved one and revisions must have no gaps',
            NEW.source_series, NEW.publication_year, max_rev USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' AND NOT EXISTS (
        SELECT 1 FROM energy_publication_candidates c
        WHERE c.accepted_publication_id = NEW.id AND c.review_status = 'accepted' AND c.source_series = NEW.source_series
          AND c.nominal_publication_year = NEW.publication_year AND c.source_url = NEW.source_url
          AND c.source_sha256 = NEW.source_sha256 AND c.source_size_bytes = NEW.source_size_bytes) THEN
        RAISE EXCEPTION 'energy publication % has no matching accepted source candidate at commit', NEW.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- An accepted candidate must correspond to its resulting publication (series, year, URL, SHA, size).
CREATE OR REPLACE FUNCTION energy_check_candidate_link() RETURNS trigger AS $$
BEGIN
    IF NEW.review_status = 'accepted' AND NOT EXISTS (
        SELECT 1 FROM energy_publications p
        WHERE p.id = NEW.accepted_publication_id AND p.source_series = NEW.source_series
          AND p.publication_year = NEW.nominal_publication_year AND p.source_url = NEW.source_url
          AND p.source_sha256 = NEW.source_sha256 AND p.source_size_bytes = NEW.source_size_bytes) THEN
        RAISE EXCEPTION 'energy candidate % does not match its accepted publication', NEW.id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------------------------------------------------------------- bindings
DROP TRIGGER IF EXISTS energy_publication_candidates_before_update ON energy_publication_candidates;
CREATE TRIGGER energy_publication_candidates_before_update BEFORE UPDATE ON energy_publication_candidates
    FOR EACH ROW EXECUTE FUNCTION energy_candidates_before_update();
DROP TRIGGER IF EXISTS energy_publication_candidates_no_delete ON energy_publication_candidates;
CREATE TRIGGER energy_publication_candidates_no_delete BEFORE DELETE ON energy_publication_candidates
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();
DROP TRIGGER IF EXISTS energy_publication_candidates_link ON energy_publication_candidates;
CREATE CONSTRAINT TRIGGER energy_publication_candidates_link AFTER INSERT OR UPDATE ON energy_publication_candidates
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_candidate_link();

DROP TRIGGER IF EXISTS energy_publications_before_insert ON energy_publications;
CREATE TRIGGER energy_publications_before_insert BEFORE INSERT ON energy_publications
    FOR EACH ROW EXECUTE FUNCTION energy_publications_before_insert();
DROP TRIGGER IF EXISTS energy_publications_before_update ON energy_publications;
CREATE TRIGGER energy_publications_before_update BEFORE UPDATE ON energy_publications
    FOR EACH ROW EXECUTE FUNCTION energy_publications_before_update();
DROP TRIGGER IF EXISTS energy_publications_no_delete ON energy_publications;
CREATE TRIGGER energy_publications_no_delete BEFORE DELETE ON energy_publications
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();
DROP TRIGGER IF EXISTS energy_publications_chain ON energy_publications;
CREATE CONSTRAINT TRIGGER energy_publications_chain AFTER INSERT OR UPDATE ON energy_publications
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_publication_chain();
DROP TRIGGER IF EXISTS energy_publications_one_current_import ON energy_publications;
CREATE CONSTRAINT TRIGGER energy_publications_one_current_import AFTER INSERT ON energy_publications
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_one_current_import();

DROP TRIGGER IF EXISTS energy_generation_imports_before_insert ON energy_generation_imports;
CREATE TRIGGER energy_generation_imports_before_insert BEFORE INSERT ON energy_generation_imports
    FOR EACH ROW EXECUTE FUNCTION energy_imports_before_insert();
DROP TRIGGER IF EXISTS energy_generation_imports_before_update ON energy_generation_imports;
CREATE TRIGGER energy_generation_imports_before_update BEFORE UPDATE ON energy_generation_imports
    FOR EACH ROW EXECUTE FUNCTION energy_imports_before_update();
DROP TRIGGER IF EXISTS energy_generation_imports_no_delete ON energy_generation_imports;
CREATE TRIGGER energy_generation_imports_no_delete BEFORE DELETE ON energy_generation_imports
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();
DROP TRIGGER IF EXISTS energy_generation_imports_one_current ON energy_generation_imports;
CREATE CONSTRAINT TRIGGER energy_generation_imports_one_current AFTER INSERT OR UPDATE ON energy_generation_imports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_one_current_import();
DROP TRIGGER IF EXISTS energy_generation_imports_counts ON energy_generation_imports;
CREATE CONSTRAINT TRIGGER energy_generation_imports_counts AFTER INSERT OR UPDATE ON energy_generation_imports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_import_counts();

DROP TRIGGER IF EXISTS energy_source_stations_immutable ON energy_source_stations;
CREATE TRIGGER energy_source_stations_immutable BEFORE UPDATE ON energy_source_stations
    FOR EACH ROW EXECUTE FUNCTION energy_reject_change();
DROP TRIGGER IF EXISTS energy_source_stations_no_delete ON energy_source_stations;
CREATE TRIGGER energy_source_stations_no_delete BEFORE DELETE ON energy_source_stations
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();

DROP TRIGGER IF EXISTS energy_generation_observations_before_insert ON energy_generation_observations;
CREATE TRIGGER energy_generation_observations_before_insert BEFORE INSERT ON energy_generation_observations
    FOR EACH ROW EXECUTE FUNCTION energy_observations_before_insert();
DROP TRIGGER IF EXISTS energy_generation_observations_immutable ON energy_generation_observations;
CREATE TRIGGER energy_generation_observations_immutable BEFORE UPDATE ON energy_generation_observations
    FOR EACH ROW EXECUTE FUNCTION energy_reject_change();
DROP TRIGGER IF EXISTS energy_generation_observations_no_delete ON energy_generation_observations;
CREATE TRIGGER energy_generation_observations_no_delete BEFORE DELETE ON energy_generation_observations
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();
DROP TRIGGER IF EXISTS energy_generation_observations_counts ON energy_generation_observations;
CREATE CONSTRAINT TRIGGER energy_generation_observations_counts AFTER INSERT ON energy_generation_observations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_import_counts();

DROP TRIGGER IF EXISTS energy_generation_group_totals_immutable ON energy_generation_group_totals;
CREATE TRIGGER energy_generation_group_totals_immutable BEFORE UPDATE ON energy_generation_group_totals
    FOR EACH ROW EXECUTE FUNCTION energy_reject_change();
DROP TRIGGER IF EXISTS energy_generation_group_totals_no_delete ON energy_generation_group_totals;
CREATE TRIGGER energy_generation_group_totals_no_delete BEFORE DELETE ON energy_generation_group_totals
    FOR EACH ROW EXECUTE FUNCTION energy_reject_delete();
DROP TRIGGER IF EXISTS energy_generation_group_totals_counts ON energy_generation_group_totals;
CREATE CONSTRAINT TRIGGER energy_generation_group_totals_counts AFTER INSERT ON energy_generation_group_totals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION energy_check_import_counts();

-- ---------------------------------------------------------------------------------------------------------------------- views
-- Accepted publications (approved or superseded) mapped to their one current import. It does not decide canonical vintage.
CREATE OR REPLACE VIEW energy_publication_current_imports AS
SELECT p.id AS publication_id, p.source_series, p.publication_year, p.revision_sequence, p.publication_status,
       p.title, p.source_url, p.source_sha256, p.rights_status,
       i.id AS import_id, i.import_sequence, i.import_status
FROM energy_publications p
JOIN energy_generation_imports i ON i.publication_id = p.id AND i.import_status = 'current';

-- Canonical annual generation: per (source series, station identity, year) the row of the newest APPROVED publication (by
-- publication_year DESC, revision_sequence DESC; import_sequence never takes part) whose CURRENT import contains it. Omission falls
-- back to an older approved publication; an explicit newer not_reported row wins (no fallback). is_latest_publication compares the
-- selected publication with the greatest approved (publication_year, revision_sequence) of the series, independent of the row.
CREATE OR REPLACE VIEW energy_generation_canonical AS
WITH eligible AS (
    SELECT * FROM energy_publication_current_imports WHERE publication_status = 'approved'
), latest AS (
    SELECT DISTINCT ON (source_series) source_series, publication_id AS latest_publication_id
    FROM eligible
    ORDER BY source_series, publication_year DESC, revision_sequence DESC
)
SELECT DISTINCT ON (o.source_station_id, o.observation_year)
       e.source_series, s.id AS source_station_id, s.station_group, s.station_name, o.observation_year,
       o.value_state, o.generation_gwh, o.published_precision,
       e.publication_id, e.publication_year, e.revision_sequence, e.import_id, e.import_sequence,
       o.source_table, o.source_pdf_page, o.source_printed_page,
       (e.publication_id = l.latest_publication_id) AS is_latest_publication
FROM energy_generation_observations o
JOIN eligible e ON e.import_id = o.import_id
JOIN energy_source_stations s ON s.id = o.source_station_id AND s.source_series = e.source_series
JOIN latest l ON l.source_series = e.source_series
ORDER BY o.source_station_id, o.observation_year, e.publication_year DESC, e.revision_sequence DESC;
