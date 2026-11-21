"""PostgreSQL persistence for the vetted ERB 2025 Annual Statistical Bulletin (Energy generation by station, Tables 13-16).

Implements the first-import and idempotent re-import workflows of docs/design/energy-generation-storage-vintages.md (sections 9, 12, 13)
on top of migration 014, which stays authoritative: its deferred constraint triggers re-check counts, the single current import, the
publication chain and the candidate link at COMMIT. Nothing here creates schema objects. Every Energy relation is schema-qualified
(`public.energy_*`) so neither the caller's search_path nor a TEMP table with an Energy name can redirect a statement.

Scope: first accepted publication and an identical re-import (no-op). Official replacement (revision 2) and reviewed import correction
(import 2) are deliberately not implemented: a same-SHA difference fails closed. Parser diagnostics (footing residuals, Table 12 check)
are validated before storage and never stored. Rights status stays review_required (local/internal use only).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.services.erb_generation_models import (
    FOOTING_COMPUTED, FOOTING_PARTIAL, NOT_REPORTED, REPORTED, ErbGenerationError, ParseResult,
)
from app.services.erb_generation_source import BULLETIN_2025, BulletinManifest, parse_bulletin
from app.services.erb_generation_table import (
    EXPECTED_RESIDUALS, EXPECTED_TABLE12_DIFFERENCE, EXPECTED_TABLE12_RESIDUAL, PINNED_PAGES, TABLES, YEARS,
)

SOURCE_SERIES = "erb_statistical_bulletin"
PUBLISHER = "ERB"
FIRST_REVISION = 1
IMPORTER_VERSION = "erb_generation_importer/1"  # audit metadata only; never part of dataset equality
IMPORTED = "imported"
ALREADY_PRESENT = "already_present"

_REQUIRED_RELATIONS = (
    "energy_publication_candidates", "energy_publications", "energy_generation_imports", "energy_source_stations",
    "energy_generation_observations", "energy_generation_group_totals",
)
# (table, trigger, function, firing, deferred constraint trigger?) exactly as migration 014 defines them; every one is FOR EACH ROW. A name
# alone proves nothing: each must sit on the expected public table, call the expected public function, be enabled for normal sessions and
# fire at exactly the expected timing and events (AFTER INSERT is not AFTER INSERT OR UPDATE; BEFORE is not AFTER).
_REQUIRED_TRIGGERS = (
    ("energy_publications", "energy_publications_before_insert", "energy_publications_before_insert", "BEFORE INSERT", False),
    ("energy_publications", "energy_publications_before_update", "energy_publications_before_update", "BEFORE UPDATE", False),
    ("energy_publications", "energy_publications_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_publications", "energy_publications_chain", "energy_check_publication_chain", "AFTER INSERT OR UPDATE", True),
    ("energy_publications", "energy_publications_one_current_import", "energy_check_one_current_import", "AFTER INSERT", True),
    ("energy_publication_candidates", "energy_publication_candidates_before_update", "energy_candidates_before_update", "BEFORE UPDATE", False),
    ("energy_publication_candidates", "energy_publication_candidates_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_publication_candidates", "energy_publication_candidates_link", "energy_check_candidate_link", "AFTER INSERT OR UPDATE", True),
    ("energy_generation_imports", "energy_generation_imports_before_insert", "energy_imports_before_insert", "BEFORE INSERT", False),
    ("energy_generation_imports", "energy_generation_imports_before_update", "energy_imports_before_update", "BEFORE UPDATE", False),
    ("energy_generation_imports", "energy_generation_imports_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_generation_imports", "energy_generation_imports_one_current", "energy_check_one_current_import", "AFTER INSERT OR UPDATE", True),
    ("energy_generation_imports", "energy_generation_imports_counts", "energy_check_import_counts", "AFTER INSERT OR UPDATE", True),
    ("energy_source_stations", "energy_source_stations_immutable", "energy_reject_change", "BEFORE UPDATE", False),
    ("energy_source_stations", "energy_source_stations_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_generation_observations", "energy_generation_observations_before_insert", "energy_observations_before_insert", "BEFORE INSERT", False),
    ("energy_generation_observations", "energy_generation_observations_immutable", "energy_reject_change", "BEFORE UPDATE", False),
    ("energy_generation_observations", "energy_generation_observations_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_generation_observations", "energy_generation_observations_counts", "energy_check_import_counts", "AFTER INSERT", True),
    ("energy_generation_group_totals", "energy_generation_group_totals_immutable", "energy_reject_change", "BEFORE UPDATE", False),
    ("energy_generation_group_totals", "energy_generation_group_totals_no_delete", "energy_reject_delete", "BEFORE DELETE", False),
    ("energy_generation_group_totals", "energy_generation_group_totals_counts", "energy_check_import_counts", "AFTER INSERT", True),
)
_TRIGGER_CATALOG = text(
    "SELECT c.relname AS tbl, t.tgname AS name, pn.nspname AS fn_schema, p.proname AS fn, t.tgenabled AS enabled, "
    "t.tgtype AS tgtype, (t.tgconstraint <> 0 AND t.tgdeferrable AND t.tginitdeferred) AS deferred "
    "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
    "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace "
    "WHERE NOT t.tgisinternal AND c.relname = ANY(:tables)"
)
_GROUP_OF_TABLE = {t.name: t.group for t in TABLES}
_SPEC_OF_TABLE = {t.name: t for t in TABLES}


class EnergySchemaNotMigratedError(Exception):
    """Energy schema not migrated: migration 014 is not installed in `public`. The importer never applies migrations."""


class EnergyCandidateError(Exception):
    """The vetted source candidate is missing, not pending, or does not match the manifest. Nothing was written."""


class EnergyImportConflictError(Exception):
    """The accepted publication exists but the new parse or manifest differs from what is stored. Fail closed: review required
    (the reviewed import-correction workflow, design section 13.5, is not implemented). Nothing was written."""


@dataclass(frozen=True)
class ImportResult:
    status: str  # IMPORTED or ALREADY_PRESENT
    publication_id: int
    revision_sequence: int
    import_id: int
    import_sequence: int
    observations_inserted: int
    totals_inserted: int
    stations_inserted: int
    stations_reused: int


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: int
    created: bool


# ----------------------------------------------------------------------------------------------- completeness manifest
def expected_observation_keys() -> frozenset[tuple[str, str, int]]:
    """(station_group, station_name, observation_year) for every pinned station column and year: 30 x 13 = 390."""
    return frozenset((t.group, s, y) for t in TABLES for s in t.stations for y in YEARS)


def expected_total_keys() -> frozenset[tuple[str, int]]:
    """(station_group, observation_year): 4 x 13 = 52."""
    return frozenset((t.group, y) for t in TABLES for y in YEARS)


def _compare_keys(label: str, parsed: list, expected: frozenset) -> None:
    unique = set(parsed)
    duplicates = len(parsed) - len(unique)
    missing, unexpected = expected - unique, unique - expected
    if duplicates or missing or unexpected:
        raise ErbGenerationError(f"{label}: key set differs from the vetted manifest "
                                 f"({len(missing)} missing, {len(unexpected)} unexpected, {duplicates} duplicate)")


def validate_parse_result(result: ParseResult, manifest: BulletinManifest = BULLETIN_2025) -> None:
    """All pre-transaction checks: identity, exact key sets (never counts alone), per-row consistency and the pinned diagnostics."""
    if (result.source_url, result.source_sha256, result.source_publication, result.publication_year) != (
            manifest.url, manifest.sha256, manifest.title, manifest.publication_year):
        raise ErbGenerationError("parse result does not carry the vetted bulletin identity")
    _compare_keys("observations", [(o.station_group, o.station_name, o.observation_year) for o in result.observations],
                  expected_observation_keys())
    totals = [(_GROUP_OF_TABLE.get(t.source_table), t.observation_year) for t in result.table_totals]
    _compare_keys("group totals", totals, expected_total_keys())
    for o in result.observations:
        spec = _SPEC_OF_TABLE.get(o.source_table)
        if spec is None or spec.group != o.station_group or not _page_belongs(spec, o.source_pdf_page, o.source_printed_page):
            raise ErbGenerationError("observation provenance differs from the pinned tables and pages")
        if o.value_state == REPORTED and isinstance(o.generation_gwh, Decimal) and o.generation_gwh.is_finite():
            continue
        if o.value_state == NOT_REPORTED and o.generation_gwh is None:
            continue
        raise ErbGenerationError("observation value state is inconsistent with its value")
    for t in result.table_totals:
        spec = _SPEC_OF_TABLE.get(t.source_table)
        if spec is None or not _page_belongs(spec, t.source_pdf_page, PINNED_PAGES.get(t.source_pdf_page)) or not t.published_total_gwh.is_finite():
            raise ErbGenerationError("group total provenance or value is invalid")
    _check_diagnostics(result)


def _page_belongs(spec, pdf_page: int, printed_page: int | None) -> bool:
    """The claimed PDF page must be one of THIS table's pinned pages (Table 14 spans two) and map to the pinned printed page."""
    return pdf_page in spec.pdf_pages and printed_page == PINNED_PAGES[pdf_page]


def _check_diagnostics(result: ParseResult) -> None:
    """The diagnostics must be the ones derivable from the incoming observations and totals, and must satisfy the parser's pinned policy
    (constants reused from erb_generation_table). They are validated here and never persisted. Runs before any database access."""
    expected_keys = {(t.name, y) for t in TABLES for y in YEARS}
    supplied = [(f.source_table, f.observation_year) for f in result.footing]
    if len(supplied) != len(set(supplied)) or set(supplied) != expected_keys:
        raise ErbGenerationError("footing diagnostics do not cover exactly Tables 13-16 x 2013-2025")
    stations: dict[tuple[str, int], list[Decimal | None]] = {}
    for o in result.observations:
        stations.setdefault((o.source_table, o.observation_year), []).append(o.generation_gwh)
    totals = {(t.source_table, t.observation_year): t.published_total_gwh for t in result.table_totals}
    for f in result.footing:
        key = (f.source_table, f.observation_year)
        values = stations[key]
        missing = sum(1 for v in values if v is None)
        reported_sum = sum((v for v in values if v is not None), Decimal(0))
        residual = totals[key] - reported_sum
        derived = (missing, FOOTING_PARTIAL if missing else FOOTING_COMPUTED, reported_sum, totals[key], residual)
        if (f.stations_not_reported, f.status, f.reported_sum_gwh, f.published_total_gwh, f.residual_gwh) != derived:
            raise ErbGenerationError(f"{f.source_table} {f.observation_year}: footing diagnostic disagrees with the incoming data")
        number = int(f.source_table.split()[1])
        if residual != EXPECTED_RESIDUALS.get((number, f.observation_year), Decimal(0)):
            raise ErbGenerationError(f"{f.source_table} {f.observation_year}: footing residual differs from the pinned source value")
    t12 = result.table12
    if (t12.table16_total_gwh != totals[("Table 16", 2025)]
            or t12.table16_minus_table12_ipp_gwh != t12.table16_total_gwh - t12.table12_ipp_gwh
            or t12.table12_footing_residual_gwh != t12.table12_printed_total_gwh - t12.table12_rows_sum_gwh):
        raise ErbGenerationError("Table 12 diagnostic disagrees with the incoming Table 16 total or its own arithmetic")
    if (t12.table16_minus_table12_ipp_gwh != EXPECTED_TABLE12_DIFFERENCE
            or t12.table12_footing_residual_gwh != EXPECTED_TABLE12_RESIDUAL):
        raise ErbGenerationError("Table 12 / Table 16 relationship differs from the pinned source values")


def _precision(value: Decimal) -> int:
    """Printed scale. The parser builds each Decimal directly from the printed token (separators removed), which keeps its exponent."""
    return -value.as_tuple().exponent


def observation_rows(result: ParseResult) -> dict[tuple[str, str, int], tuple]:
    """Stable fields of every observation (section 12.3), keyed by (group, station, year)."""
    return {
        (o.station_group, o.station_name, o.observation_year): (
            o.value_state, o.generation_gwh, None if o.generation_gwh is None else _precision(o.generation_gwh),
            o.source_table, o.source_pdf_page, o.source_printed_page)
        for o in result.observations
    }


def total_rows(result: ParseResult) -> dict[tuple[str, int], tuple]:
    return {
        (_GROUP_OF_TABLE[t.source_table], t.observation_year): (
            t.published_total_gwh, _precision(t.published_total_gwh), t.source_table, t.source_pdf_page, PINNED_PAGES[t.source_pdf_page])
        for t in result.table_totals
    }


# --------------------------------------------------------------------------------------------------------- SQL helpers
def _firing(tgtype: int) -> tuple[str, bool]:
    """Decode pg_trigger.tgtype bits (1 ROW, 2 BEFORE, 4 INSERT, 8 DELETE, 16 UPDATE, 32 TRUNCATE, 64 INSTEAD OF) into
    ("<TIMING> <EVENT> [OR <EVENT>...]", for_each_row), e.g. ("AFTER INSERT OR UPDATE", True)."""
    timing = "INSTEAD OF" if tgtype & 64 else "BEFORE" if tgtype & 2 else "AFTER"
    events = " OR ".join(name for bit, name in ((4, "INSERT"), (16, "UPDATE"), (8, "DELETE"), (32, "TRUNCATE")) if tgtype & bit)
    return f"{timing} {events}", bool(tgtype & 1)


def _require_schema(c: Connection) -> None:
    """Migration 014 must be installed COMPLETELY: the six tables and every lifecycle/constraint trigger the importer relies on, each on
    its expected public table, bound to its expected public function and enabled. Catalog lookups only (no search_path dependence)."""
    problems = [r for r in _REQUIRED_RELATIONS if c.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{r}"}).scalar() is None]
    if not problems:
        found = {(r.tbl, r.name): r for r in c.execute(_TRIGGER_CATALOG, {"tables": list(_REQUIRED_RELATIONS)})}
        for table, trigger, function, firing, deferred in _REQUIRED_TRIGGERS:
            row = found.get((table, trigger))
            if row is None:
                problems.append(f"trigger {trigger}")
            elif (row.fn_schema, row.fn) != ("public", function):
                problems.append(f"trigger {trigger} (bound to the wrong function)")
            elif row.enabled not in ("O", "A"):  # origin (default) or always; disabled (D) and replica-only (R) never fire normally
                problems.append(f"trigger {trigger} (not enabled)")
            elif _firing(row.tgtype) != (firing, True):
                problems.append(f"trigger {trigger} (fires {_firing(row.tgtype)[0]}, expected {firing}, FOR EACH ROW)")
            elif deferred and not row.deferred:
                problems.append(f"trigger {trigger} (not a deferred constraint trigger)")
    if problems:
        raise EnergySchemaNotMigratedError(f"Energy schema not migrated: migration 014 is incomplete ({', '.join(problems)}); "
                                           "apply it first. Nothing was written")


def _lock(c: Connection, year: int) -> None:
    c.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"energy_publication:{SOURCE_SERIES}:{year}"})


_CANDIDATE_COLUMNS = "id, source_series, nominal_publication_year, source_url, source_sha256, source_size_bytes, review_status"


def _candidate_matches(row, m: BulletinManifest) -> bool:
    return (row.source_series, row.nominal_publication_year, row.source_url, row.source_sha256, row.source_size_bytes) == (
        SOURCE_SERIES, m.publication_year, m.url, m.sha256, m.size_bytes)


def register_vetted_candidate(engine: Engine, *, discovered_at: datetime, manifest: BulletinManifest = BULLETIN_2025) -> CandidateResult:
    """Explicit review operation: record the vetted bulletin as a PENDING source candidate (design 13.1 step 1, 13.7). Only the pinned
    BULLETIN_2025 manifest can be registered, so changed bytes never become a candidate by themselves. Idempotent."""
    if manifest != BULLETIN_2025:
        raise EnergyCandidateError("only the vetted ERB 2025 bulletin manifest can be registered as a candidate")
    with engine.begin() as c:
        _require_schema(c)
        _lock(c, manifest.publication_year)
        row = c.execute(text(f"SELECT {_CANDIDATE_COLUMNS} FROM public.energy_publication_candidates WHERE source_sha256 = :h"),
                        {"h": manifest.sha256}).one_or_none()
        if row is not None:
            if not _candidate_matches(row, manifest):
                raise EnergyCandidateError("a candidate with the vetted SHA-256 exists with different source metadata")
            return CandidateResult(row.id, False)
        new_id = c.execute(text(
            "INSERT INTO public.energy_publication_candidates (source_series, nominal_publication_year, source_url, source_sha256, "
            "source_size_bytes, discovered_at) VALUES (:s, :y, :u, :h, :z, :t) RETURNING id"),
            {"s": SOURCE_SERIES, "y": manifest.publication_year, "u": manifest.url, "h": manifest.sha256, "z": manifest.size_bytes,
             "t": discovered_at}).scalar_one()
        return CandidateResult(new_id, True)


def _publication_matches(row, m: BulletinManifest) -> bool:
    """Section 12.1: publication metadata compared exactly (revision 1 has no predecessor)."""
    return (row.publisher, row.source_series, row.publication_year, row.revision_sequence, row.supersedes_publication_id, row.title,
            row.cover_date, row.source_url, row.source_sha256, row.source_size_bytes, row.pdf_pages, row.rights_status) == (
        PUBLISHER, SOURCE_SERIES, m.publication_year, FIRST_REVISION, None, m.title, m.cover_date, m.url, m.sha256, m.size_bytes,
        m.pdf_pages, m.rights_status)


def _stored_dataset(c: Connection, import_id: int):
    observations = {
        (r.station_group, r.station_name, r.observation_year): (
            r.value_state, r.generation_gwh, r.published_precision, r.source_table, r.source_pdf_page, r.source_printed_page)
        for r in c.execute(text(
            "SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision, "
            "o.source_table, o.source_pdf_page, o.source_printed_page FROM public.energy_generation_observations o "
            "JOIN public.energy_source_stations s ON s.id = o.source_station_id WHERE o.import_id = :i"), {"i": import_id})
    }
    totals = {
        (r.station_group, r.observation_year): (r.total_gwh, r.published_precision, r.source_table, r.source_pdf_page, r.source_printed_page)
        for r in c.execute(text(
            "SELECT station_group, observation_year, total_gwh, published_precision, source_table, source_pdf_page, source_printed_page "
            "FROM public.energy_generation_group_totals WHERE import_id = :i"), {"i": import_id})
    }
    return observations, totals


def _existing_outcome(c: Connection, pub, result: ParseResult, m: BulletinManifest) -> ImportResult:
    """The accepted publication already exists: identical stable data is a no-op, anything else is a conflict. Writes nothing."""
    if not _publication_matches(pub, m) or pub.publication_status != "approved":
        raise EnergyImportConflictError("the stored publication differs from the vetted manifest or is not approved; review required")
    current = c.execute(text("SELECT id, import_sequence, expected_observation_count, expected_group_total_count "
                             "FROM public.energy_generation_imports WHERE publication_id = :p AND import_status = 'current'"),
                        {"p": pub.id}).one()
    observations, totals = observation_rows(result), total_rows(result)
    stored_obs, stored_totals = _stored_dataset(c, current.id)
    if (current.expected_observation_count, current.expected_group_total_count) != (len(observations), len(totals)):
        raise EnergyImportConflictError("the current import's stored expectations differ from the parsed dataset; review required")
    if (stored_obs.keys() != observations.keys() or stored_totals.keys() != totals.keys()
            or any(stored_obs[k] != v for k, v in observations.items())  # precision is its own tuple field
            or any(stored_totals[k] != v for k, v in totals.items())):
        raise EnergyImportConflictError("the same publication and SHA-256 produce different stable output; review required "
                                        "(no import revision is created by the ordinary path)")
    return ImportResult(ALREADY_PRESENT, pub.id, pub.revision_sequence, current.id, current.import_sequence, 0, 0, 0, 0)


# --------------------------------------------------------------------------------------------------------------- import
def import_erb_2025(
    engine: Engine, result: ParseResult, *, retrieved_at: datetime, manifest: BulletinManifest = BULLETIN_2025,
    importer_version: str = IMPORTER_VERSION,
) -> ImportResult:
    """Persist (or confirm) the vetted ERB 2025 publication from an already parsed bulletin. Validation runs before any connection is
    opened; the whole first import is one transaction whose deferred database constraints decide the commit."""
    if manifest != BULLETIN_2025:
        raise ErbGenerationError("only the vetted ERB 2025 bulletin manifest can be imported")
    validate_parse_result(result, manifest)
    observations, totals = observation_rows(result), total_rows(result)
    with engine.begin() as c:
        _require_schema(c)
        _lock(c, manifest.publication_year)
        existing = c.execute(text("SELECT * FROM public.energy_publications WHERE source_sha256 = :h OR "
                                  "(source_series = :s AND publication_year = :y)"),
                             {"h": manifest.sha256, "s": SOURCE_SERIES, "y": manifest.publication_year}).all()
        if len(existing) > 1:
            raise EnergyImportConflictError("several publications exist for this series and year; official replacement is not "
                                            "handled by this importer; review required")
        if existing:
            return _existing_outcome(c, existing[0], result, manifest)
        return _first_import(c, manifest, observations, totals, retrieved_at, importer_version)


def _first_import(c: Connection, m: BulletinManifest, observations: dict, totals: dict, retrieved_at: datetime,
                  importer_version: str) -> ImportResult:
    cand = c.execute(text(f"SELECT {_CANDIDATE_COLUMNS} FROM public.energy_publication_candidates WHERE source_sha256 = :h FOR UPDATE"),
                     {"h": m.sha256}).one_or_none()
    if cand is None:
        raise EnergyCandidateError("no source candidate is registered for the vetted bulletin; register_vetted_candidate first")
    if cand.review_status != "pending" or not _candidate_matches(cand, m):
        raise EnergyCandidateError("the vetted bulletin's candidate is not a pending, matching candidate")
    publication_id = c.execute(text(
        "INSERT INTO public.energy_publications (publisher, source_series, publication_year, revision_sequence, publication_status, "
        "supersedes_publication_id, title, cover_date, source_url, source_sha256, source_size_bytes, pdf_pages, rights_status, "
        "first_retrieved_at) VALUES (:pub, :s, :y, :r, 'approved', NULL, :title, :cover, :u, :h, :z, :pages, :rights, :t) RETURNING id"),
        {"pub": PUBLISHER, "s": SOURCE_SERIES, "y": m.publication_year, "r": FIRST_REVISION, "title": m.title, "cover": m.cover_date,
         "u": m.url, "h": m.sha256, "z": m.size_bytes, "pages": m.pdf_pages, "rights": m.rights_status, "t": retrieved_at}).scalar_one()

    station_keys = sorted({(g, n) for g, n, _ in observations})
    existing_ids = {(r.station_group, r.station_name): r.id for r in c.execute(
        text("SELECT id, station_group, station_name FROM public.energy_source_stations WHERE source_series = :s"), {"s": SOURCE_SERIES})}
    inserted = 0
    for group, name in station_keys:
        if (group, name) not in existing_ids:
            existing_ids[(group, name)] = c.execute(text(
                "INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES (:s, :g, :n) RETURNING id"),
                {"s": SOURCE_SERIES, "g": group, "n": name}).scalar_one()
            inserted += 1

    import_id = c.execute(text(
        "INSERT INTO public.energy_generation_imports (publication_id, import_sequence, import_status, expected_observation_count, "
        "expected_group_total_count, importer_version, correction_reason) VALUES (:p, 1, 'current', :o, :t, :v, NULL) RETURNING id"),
        {"p": publication_id, "o": len(observations), "t": len(totals), "v": importer_version}).scalar_one()
    c.execute(text(
        "INSERT INTO public.energy_generation_observations (import_id, source_station_id, observation_year, value_state, generation_gwh, "
        "published_precision, source_table, source_pdf_page, source_printed_page) VALUES "
        "(:i, :st, :y, :state, :v, :prec, :tbl, :pdf, :printed)"),
        [{"i": import_id, "st": existing_ids[(g, n)], "y": y, "state": s, "v": v, "prec": p, "tbl": tbl, "pdf": pdf, "printed": pr}
         for (g, n, y), (s, v, p, tbl, pdf, pr) in sorted(observations.items())])
    c.execute(text(
        "INSERT INTO public.energy_generation_group_totals (import_id, station_group, observation_year, total_gwh, published_precision, "
        "source_table, source_pdf_page, source_printed_page) VALUES (:i, :g, :y, :v, :prec, :tbl, :pdf, :printed)"),
        [{"i": import_id, "g": g, "y": y, "v": v, "prec": p, "tbl": tbl, "pdf": pdf, "printed": pr}
         for (g, y), (v, p, tbl, pdf, pr) in sorted(totals.items())])
    _accept_candidate(c, cand.id, publication_id, m)
    return ImportResult(IMPORTED, publication_id, FIRST_REVISION, import_id, 1, len(observations), len(totals), inserted,
                        len(station_keys) - inserted)


def _accept_candidate(c: Connection, candidate_id: int, publication_id: int, m: BulletinManifest) -> None:
    c.execute(text("UPDATE public.energy_publication_candidates SET review_status = 'accepted', review_note = :note, "
                   "reviewed_at = now(), accepted_publication_id = :p WHERE id = :id"),
              {"note": f"Accepted by the vetted manifest: {m.title} ({m.cover_date}), pinned SHA-256", "p": publication_id, "id": candidate_id})


def import_bulletin_bytes(engine: Engine, pdf_bytes: bytes, *, retrieved_at: datetime, extractor=None) -> ImportResult:
    """Convenience entry: verify the bytes against the pinned size/SHA-256 (changed bytes raise before anything is written), parse, import."""
    result = parse_bulletin(pdf_bytes) if extractor is None else parse_bulletin(pdf_bytes, extractor)
    return import_erb_2025(engine, result, retrieved_at=retrieved_at)
