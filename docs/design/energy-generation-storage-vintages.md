# Energy generation: storage and publication-vintage design

Status: **design only** (revision 2, 2026-11-15, branch `design/energy-generation-storage-vintages`, parser base `953f5e9`). Nothing here is implemented: no migration, SQL, store function, API route, frontend or backfill job exists, and the design is under review. Rights for all ERB data remain `review_required` (local/internal use only). This revision closes every policy decision that migration 014 would otherwise have to invent (section 19).

## 1. Goals

- Store exactly what a specific ERB publication revision reported for a specific station and observation year, without ever replacing a published value.
- Keep publication vintage, same-year corrected files, restatements and provenance queryable.
- Preserve `reported` versus `not_reported` (a printed dash); never turn a dash into zero.
- Give reads a deterministic, documented "current" value that is derived, not stored by overwriting.
- Stay small, strongly typed and consistent with the repo's conventions (`db/init/NNN_*.sql`, idempotent `CREATE ... IF NOT EXISTS`, `NUMERIC` plus a published-precision column, conflict-aware atomic imports).

## 2. Non-goals

- No migration, importer, store function, API route, frontend or backfill in this task.
- No master plant registry and no automatic station merging or entity resolution.
- No generic metric/EAV system.
- No storage of raw PDF bytes, extracted page text or parser-integrity diagnostics (section 13).
- Storage is not permission to expose data (section 16).

## 3. Source behavior (what the design must absorb)

Verified by reading the pinned 2025 bulletin and, for comparison, the downloaded 2024 bulletin and the 2024/2025 Energy Sector Reports. The cross-bulletin comparison below was a one-off reading of table text, not parser output.

| Observation | Evidence |
|---|---|
| ERB republishes history and **table numbers change per publication** | Bulletin 2024 prints the generation tables as Tables 21-24 (years 2013-2024); the pinned 2025 bulletin prints them as Tables 13-16 (2013-2025). |
| **Station sets grow between vintages** | The 2024 bulletin's IPP table has 8 station columns and total 5,848.01 GWh for 2024; the 2025 bulletin's Table 16 has 14 and prints 6,096.15 GWh for 2024. The 8 shared stations carry identical values; the total changed because columns were added (Dangote, CEC Emergency Generators, ...). Column order is also publication-specific. |
| **A dash in one vintage becomes a printed `0` in the next** | Comparing the IPP table across the two bulletins for the 8 shared stations and 2013-2024: 96 cells; 68 numerically equal, **28 dash in the 2024 bulletin and `0` in the 2025 bulletin**, 0 changed numbers. Example: `Bangweulu Power Limited` 2013 is `-` then `0`. |
| Same number, different printed precision | 3 cells: `393.40` (2024 bulletin) versus `393.4` (2025 bulletin). |
| Large hydro and ZESCO diesel tables: no change | Large hydro 2013-2024: 36 of 36 cells numerically equal. ZESCO diesel: 39 equal and 45 dash-to-dash, 0 changed. (The mini-hydro comparison was not completed and is not claimed.) |
| National totals are restated | National generation for 2024 is 13,324.38 GWh in the 2024 Energy Sector Report and 13,566.52 GWh in the 2025 report (the text says 13,568). |
| Same publication year, different documents | The 2025 bulletin and the 2025 Energy Sector Report report slightly different station values for 2025 (for example Kafue Gorge Mini Hydro 81.00 versus 81.04). A "vintage" is a specific document, not a year. |
| The URL is not a stable version | `esr2025.pdf` and `esr2026.pdf` are byte-identical files. |
| A dash has no published legend | ERB defines neither `-` nor `0`; the dash-to-zero changes show a dash is not a reliable "withdrawn" marker. |

## 4. Design options

**Option A, side-by-side publication vintages.** Every (publication revision, station, year) is its own immutable row. "Current" is a derived selection over those rows.
- Simple: insert-only data, no update logic for published values, no canonical/history divergence possible.
- Provenance is intrinsic: each row is exactly what one document said.
- Revision history is a plain query over the rows.
- Query cost is a `DISTINCT ON` or window over a few thousand rows per publication; trivial at this size.
- A wrong import is handled by marking the publication `rejected` (section 7), never by editing or deleting rows.

**Option B, canonical row plus revision-history table.** A mutable "current" table plus append-only history. Duplicates every value, needs update mechanics on each import, and a failure between the two writes leaves canonical and history divergent. It adds exactly the overwrite path this project avoids.

**Option C, event/revision model.** Each publication emits revision events relative to the previous value. Entangles classification with storage and needs reconstruction to read any value; unnecessary for annual data with a few hundred observations per publication.

**Recommendation: Option A**, with the "current" value derived by a view (section 10).

## 5. Recommended model

1. **Immutable published layer**: `energy_generation_observations` (what one publication revision reported for one source station and year) and `energy_generation_group_totals` (the printed Total column), plus `energy_publications` and `energy_source_stations`.
2. **Derived canonical layer**: a SQL **view** (`energy_generation_canonical`). A materialized view or a selection table is justified only by a measured read cost or a manual-curation need; neither is expected at a few hundred rows per publication. The view never mutates or deletes anything.
3. **Snapshot reads**: an exact publication revision's contents, never mixing vintages (section 9).

Typed per-metric tables are preferred over a generic metric dimension (section 6 notes).

## 6. Schema proposal (concept, not a migration)

The first migration would be `db/init/014_energy_generation_vintages.sql`, idempotent (`IF NOT EXISTS`), creating no rows. This section lists columns and constraints; it is deliberately not SQL.

### 6.1 `energy_publications`

One row per accepted or reviewed source document revision.

| Column | Type / constraint | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `publisher` | TEXT NOT NULL, CHECK = 'ERB' | |
| `source_series` | TEXT NOT NULL | document family, e.g. `erb_statistical_bulletin`; never contains a year or revision |
| `publication_year` | SMALLINT NOT NULL, 2000..2100 | the edition year |
| `revision_sequence` | INTEGER NOT NULL, CHECK >= 1 | 1 = original; 2, 3, ... = reviewed same-year replacements |
| `publication_status` | TEXT NOT NULL, CHECK IN ('pending','approved','superseded','rejected') | administrative (section 7) |
| `supersedes_publication_id` | BIGINT NULL, FK to `energy_publications` | the revision this one replaces |
| `title` | TEXT NOT NULL | as printed |
| `cover_date` | TEXT NULL | as printed ("February 2026"); not parsed |
| `source_url` | TEXT NOT NULL | |
| `source_sha256` | TEXT NOT NULL, CHECK 64 lowercase hex | |
| `source_size_bytes` | BIGINT NOT NULL, CHECK > 0 | |
| `pdf_pages` | INTEGER NOT NULL, CHECK > 0 | |
| `rights_status` | TEXT NOT NULL, CHECK = 'review_required' | loosening is a deliberate migration |
| `expected_observation_count` | INTEGER NOT NULL, CHECK >= 0 | audit record of the manifest expectation (section 8) |
| `expected_group_total_count` | INTEGER NOT NULL, CHECK >= 0 | same |
| `first_retrieved_at` | TIMESTAMPTZ NOT NULL | operational |
| `imported_at` | TIMESTAMPTZ NOT NULL | operational |
| `status_changed_at` | TIMESTAMPTZ NOT NULL | administrative |
| `status_note` | TEXT NULL | administrative (review reason) |

Constraints:
- `UNIQUE (source_series, publication_year, revision_sequence)`: the publication identity. It allows `2025 r1` and `2025 r2` to coexist permanently; nothing is unique on `(source_series, publication_year)` alone.
- `UNIQUE (source_sha256)`: the same bytes can never appear under two identities.
- `UNIQUE (supersedes_publication_id)`: a revision is replaced by at most one revision.
- `UNIQUE (id, source_series, publication_year)` and a composite FK `(supersedes_publication_id, source_series, publication_year)` referencing it: a replacement must belong to the **same series and the same nominal year** as the revision it replaces (the composite FK is skipped when `supersedes_publication_id` is NULL).
- `CHECK ((revision_sequence = 1) = (supersedes_publication_id IS NULL))`: revision 1 replaces nothing; every later revision names its predecessor.
- `CHECK (supersedes_publication_id <> id)`.
- A higher `revision_sequence` than the replaced revision, a consecutive sequence (`n` replaces `n - 1`), and a legal status transition cannot be expressed as column CHECKs; they are enforced by the trigger below and the supersession transaction (section 12.3).

### 6.2 `energy_source_stations`

Station identities exactly as printed, scoped to a source series. Not a master plant registry.

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `source_series` | TEXT NOT NULL (same value domain as publications, e.g. `erb_statistical_bulletin`) |
| `station_group` | TEXT NOT NULL, CHECK IN ('zesco_large_hydro','zesco_mini_hydro','zesco_diesel','independent_power_producer') |
| `station_name` | TEXT NOT NULL, CHECK non-empty and trimmed |

`UNIQUE (source_series, station_group, station_name)`. Table number is not part of the identity because table numbers change between vintages.

### 6.3 `energy_generation_observations`

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `publication_id` | BIGINT NOT NULL, FK `energy_publications` |
| `source_station_id` | BIGINT NOT NULL, FK `energy_source_stations` |
| `observation_year` | SMALLINT NOT NULL, 1990..2100 |
| `value_state` | TEXT NOT NULL, CHECK IN ('reported','not_reported') |
| `generation_gwh` | NUMERIC NULL |
| `published_precision` | INTEGER NULL |
| `source_table` | TEXT NOT NULL (as printed in this publication, e.g. `Table 16`) |
| `source_pdf_page` | INTEGER NOT NULL, > 0 |
| `source_printed_page` | INTEGER NOT NULL, > 0 |
| `imported_at` | TIMESTAMPTZ NOT NULL (operational) |

Constraints: `UNIQUE (publication_id, source_station_id, observation_year)`; `reported` requires `generation_gwh` and `published_precision` present, `not_reported` requires both NULL; `generation_gwh` finite and `>= 0` and under a sanity bound (`< 1000000`); `scale(generation_gwh) = published_precision`. Index on `(source_station_id, observation_year, publication_id)`.

### 6.4 `energy_generation_group_totals`

The printed Total column per table-year, kept as a first-class published value and never as a station or observation.

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `publication_id` | FK `energy_publications` |
| `station_group` | same four values as stations |
| `observation_year` | 1990..2100 |
| `total_gwh` | NUMERIC NOT NULL, finite, `>= 0`, `< 1000000` |
| `published_precision` | INTEGER NOT NULL, `scale(total_gwh) = published_precision` |
| `source_table`, `source_pdf_page`, `source_printed_page` | as for observations |
| `imported_at` | operational |

`UNIQUE (publication_id, station_group, observation_year)`. Totals are included in migration 014 (section 13).

### 6.5 Immutability enforcement

Migration 014 should include triggers: `energy_source_stations`, `energy_generation_observations` and `energy_generation_group_totals` reject every `UPDATE` and `DELETE`; `energy_publications` rejects every `DELETE`, and rejects an `UPDATE` that changes any column other than `publication_status`, `status_changed_at` and `status_note`, and that follows an illegal status transition (section 7). This is a deliberate hardening beyond the existing tables' application-level discipline, justified by the one sanctioned administrative mutation. A wrong or corrupt import is therefore `rejected`, never deleted.

### 6.6 Notes

- **Metric.** The first table is `energy_generation_observations` with the unit in the column name. Future metrics (installed capacity MW, imports and exports GWh, consumption GWh, peak demand MW) get their own typed tables that reuse `energy_publications` (and stations where they apply). No EAV, no shared metric dimension until two metrics demonstrably share a query path.
- **Numeric type.** Unconstrained `NUMERIC`, like the FX and other repo tables, preserves printed scale (`393.40` stays `393.40`; a typmod such as `NUMERIC(14,4)` would pad to four decimals and lose it). Observed printed precision: 0 to 2 decimals in the bulletin generation tables, up to 4 elsewhere in ERB material (`0.6519` in an Energy Sector Report diesel row). Magnitudes reach about 20,000 GWh (national totals), far under the sanity bound. `scale(value) = published_precision` makes the printed scale an enforced invariant. A future metric needing another scale gets its own column definition.
- **Source series.** One value (`erb_statistical_bulletin`) names the document family on both publications and stations; vintage lives only in the publication row (year plus revision), so there is never one series per year or per revision. The Energy Sector Report is a different series and is never mixed into bulletin canonical selection.

## 7. Publication eligibility and supersession

### 7.1 Status

| Status | Meaning | Canonical-eligible |
|---|---|---|
| `pending` | staged, not yet reviewed | no |
| `approved` | reviewed and accepted | **yes** |
| `superseded` | a reviewed replacement revision is approved | no (still queryable as history and snapshot) |
| `rejected` | reviewed and refused (unexpected/corrupt) | no |

The current pinned ERB 2025 bulletin will be inserted as `approved`, revision 1 (its approval is the code-manifest review that vetted its URL, size, SHA-256 and pages). Only `approved` publications participate in the canonical view. Legal transitions: `pending` to `approved` or `rejected`; `approved` to `superseded` (only as part of approving the replacement, section 12.3) or `rejected`; `superseded` and `rejected` are terminal (no un-supersede).

### 7.2 The one intentional administrative mutation

`publication_status`, `status_changed_at` and `status_note` are **administrative metadata, not published source content**. They are the only columns of any energy table that may ever change after insert. Every other column of every energy table is immutable. The ordinary importer never changes status (it only inserts a publication with the status its approved manifest entry declares, and compares everything else); status transitions happen only through an explicit review/administration operation (section 12.3 and section 12.4). This is the single, deliberate exception in an otherwise insert-only publication-history model.

### 7.3 Revisions and supersession

Revision identity is `(source_series, publication_year, revision_sequence)`:

- the normal original publication is revision `1`;
- an approved corrected or reissued file for the same nominal year becomes revision `2`, then `3`, and so on;
- the sequence is assigned only by explicit source review and manifest approval, **never inferred from retrieval time** or file order;
- revision is not encoded into `source_series`.

`supersedes_publication_id` names the replaced revision. Rules: same series, same nominal year, strictly higher revision sequence (consecutive: `n` replaces `n - 1`); the original and all its observations remain stored; no source metadata or observation is overwritten. Example: `erb_statistical_bulletin / 2025 / r1` exists; ERB later replaces the file at the same URL; ingestion sees a different hash and stops (section 12.4); manual review confirms a legitimate replacement; `2025 / r2` is created referencing r1; both stay queryable.

## 8. Publication-specific completeness

Completeness is **manifest-specific**. Nothing in the database hard-codes 390 observations or 52 group totals; those figures are the exact requirements of the pinned **ERB 2025 bulletin** only. Earlier or later bulletins may legitimately have fewer or more stations, different year coverage, different table numbers and different counts (for example the 2024 bulletin has 8 IPP columns and covers 2013-2024).

Each approved source manifest (in code) defines its expected publication shape:
- the expected set of source-station identities (`station_group`, `station_name`);
- the expected year coverage;
- therefore the expected observation key set and count;
- the expected group-total key set and count.

For ERB 2025 bulletin revision 1:
- 30 source-station identities (3 large hydro, 6 mini hydro, 7 diesel, 14 IPP columns);
- years 2013-2025;
- observation keys: 30 stations x 13 years = **390**;
- group-total keys: 4 groups x 13 years = **52**.

**Counts are diagnostics, not proof.** Before persistence the importer must compare the parsed **logical key sets** with the manifest's expected key sets: observations keyed by `(station_group, station_name, observation_year)` and totals keyed by `(station_group, observation_year)`. A set comparison, not a count comparison, is required, so a duplicate-plus-missing pair cannot pass because the total count happens to match. The 390/52 are consequences of the 2025 key sets.

**Stored audit expectations.** `energy_publications` stores `expected_observation_count` and `expected_group_total_count` (390 and 52 for ERB 2025 r1) to document what was expected for that publication; the full expected key sets stay in the vetted manifest/code rather than being serialized into PostgreSQL. After inserting rows and before commit, the importer asserts that the stored row counts for the publication equal the stored expectations. Migration 014 contains no global count constraint.

## 9. Canonical selection, ordering and snapshots

### 9.1 Deterministic ordering and "latest"

Within one `source_series`, publications are ordered by `(publication_year DESC, revision_sequence DESC)`. The unique identity makes the ordering total; `imported_at`, retrieval time and `publication_date` are never tie-breakers. Only `approved` publications participate.

**Latest eligible publication** has one meaning: the publication with the greatest `(publication_year, revision_sequence)` among `approved` publications in the same `source_series`, computed independently of whether any particular station or year exists in it. The result field is `is_latest_publication` (true when the selected row's publication is that publication). It is never "latest publication containing this observation".

### 9.2 Canonical rule

For each `(source_series, source_station_identity, observation_year)`, select the row from the greatest eligible `(publication_year, revision_sequence)` that **actually contains that observation**:

- **Newer approved publication omits the station/year entirely:** fall back to the latest older approved publication that contains it. Absence is not deletion; ERB does not state a withdrawal by omission and its tables change shape between vintages. The canonical row reports the older publication and `is_latest_publication = false`.
- **Newer approved publication explicitly has a dash:** the newer row wins; canonical is `value_state = not_reported`, `generation_gwh = NULL`. There is **no fallback to the older number**, because the newer publication explicitly addresses that cell. The older value stays stored and queryable, the history classifies the change as `withdrawn` (a technical label, not a claim about ERB's intent), and consumers must not read `not_reported` as zero or as proof the earlier value was wrong (ERB has no dash legend and has also turned dashes into zeros, section 3).
- **Newer approved publication reports a value where the older had a dash:** the newer value governs (`newly_reported`).
- **Same-year replacement:** approved r2 wins over r1; superseded r1 is not canonical-eligible but remains in history.

### 9.3 Exact-vintage snapshots

A snapshot selects one publication revision by `(source_series, publication_year, revision_sequence)`, for example `erb_statistical_bulletin / 2025 / r2`. It returns only the rows published in that exact revision, includes explicit `not_reported` rows, and performs **no omission fallback and no mixing with another publication**. Snapshots are available for `approved` and `superseded` revisions (a superseded revision is still a historical publication) and not for `pending` or `rejected` ones by default. A future `?vintage=` parameter must map to an exact revision, never to a bare year when several revisions exist.

### 9.4 Canonical view versus snapshot

The canonical view is **per-observation** selection and may mix vintages (omission fallback). A canonical set is therefore **not necessarily equivalent to any single ERB bulletin**; consumers who need exactly what one bulletin printed must use a snapshot. The canonical view always reports its provenance (publication id, year, revision, `is_latest_publication`) so fallbacks are never hidden.

### 9.5 Station-group vintage behavior (closed for Phase 1)

Station identity is `(source_series, station_group, station_name)`. If a later publication prints the same station name under a different group, that is a **distinct source-station identity**, preserving ERB's source classification history; no entity linking is attempted. Canonical observation sets can therefore contain identities from historical group classifications until a future explicit entity-resolution layer exists. This is not a migration blocker.

## 10. Restatement classification (designed, derived at read time)

The history of a logical observation `(source_station_id, observation_year)` is its rows across the **accepted publication revisions** (`approved` and `superseded`; never `pending` or `rejected`), ordered by `(publication_year, revision_sequence)`. History includes superseded revisions deliberately: an r1 to r2 correction is exactly a restatement a reader wants to see. Canonical selection, by contrast, uses `approved` only. Consecutive vintages are compared numerically (`Decimal` equality), and precision is compared separately:

| Earlier | Later | Class |
|---|---|---|
| none | any | `new_observation` |
| reported `a` | reported equal to `a` | `unchanged`, with `precision_changed = true` when the printed precision differs |
| reported `a` | reported `b`, `b <> a` | `revised` |
| reported | not_reported | `withdrawn` (technical label only) |
| not_reported | reported (including printed `0`) | `newly_reported` |
| not_reported | not_reported | `still_not_reported` |

For every comparison the history exposes the previous value, the previous precision, the current precision and `precision_changed`, so a precision-only change (`393.40` to `393.4`) is visible even though the numbers are equal. Real examples (section 3): the 28 IPP cells that were a dash in the 2024 bulletin and a printed `0` in the 2025 bulletin are `newly_reported`; `393.40` to `393.4` is `unchanged` with `precision_changed = true`. Station-set growth and omission are not restatements.

## 11. Stable-field equality contract

"Identical re-import" means every **stable, source-derived field** matches; it is not numeric equality alone. Operational and administrative fields never take part.

### 11.1 Publication (looked up by `source_series`, `publication_year`, `revision_sequence`)

Compared exactly: `publisher`, `source_series`, `publication_year`, `revision_sequence`, `title`, `cover_date`, `source_url`, `source_sha256`, `source_size_bytes`, `pdf_pages`, `rights_status`, `expected_observation_count`, `expected_group_total_count`, and the identity of the publication named by `supersedes_publication_id` (compared as that publication's `(source_series, year, revision)`; immutable once set). Any difference under the same identity, or the same `source_sha256` appearing under a different identity, is a **conflict**. Not compared: `publication_status`, `status_changed_at`, `status_note` (administrative).

### 11.2 Source station

Stable identity: `source_series`, `station_group`, exact `station_name`. Reuse requires an exact match of all three; no normalization, case-folding or punctuation merging; table number is not part of the identity. Since the row has no other source fields, a "different" station is simply a different identity.

### 11.3 Observation (existing `(publication_id, source_station_id, observation_year)`)

All of `value_state`, `generation_gwh`, `published_precision`, `source_table`, `source_pdf_page`, `source_printed_page` must match exactly. `Decimal("393.40")` and `Decimal("393.4")` are numerically equal but **not identical published observations** because `published_precision` differs (2 versus 1), so a precision difference is a conflict and prevents the no-op classification. NULL equals NULL for `not_reported` rows. Any difference means conflict and the whole publication transaction rolls back.

### 11.4 Group total (existing `(publication_id, station_group, observation_year)`)

All of `total_gwh`, `published_precision`, `source_table`, `source_pdf_page`, `source_printed_page` must match exactly; any difference is a conflict.

### 11.5 Excluded from equality

`first_retrieved_at`, `retrieved_at`, `imported_at`, transaction timestamps, any last-checked timestamp, `status_changed_at`, `status_note` and `publication_status`. Re-running the exact same publication later, with a different retrieval time or after an administrative status change, must remain a complete no-op.

## 12. Import and review semantics

### 12.1 Importer contract (future, no code now)

1. Verify the exact source identity (size, SHA-256) against the approved manifest entry.
2. Resolve the approved publication revision identity from the manifest (never from retrieval order).
3. Parse; the parser must succeed.
4. Validate manifest-specific completeness: the parsed key sets equal the manifest's expected key sets (section 8).
5. Validate parser diagnostics against their pinned expectations.
6. Begin one transaction.
7. Insert or compare the publication (stable fields only, section 11.1).
8. Insert or compare the source stations.
9. Insert or compare the observations.
10. Insert or compare the group totals.
11. Any conflict, count mismatch or failed check rolls back everything.
12. Commit only when the whole publication is complete (stored counts equal the stored expectations).

A failed first import leaves no partial publication data. A repeated identical import is a complete no-op (nothing inserted). A different approved revision coexists. Nothing ever updates or deletes a published row, and the ordinary importer never changes `publication_status`.

### 12.2 Schema precondition

As in the BoZ importer, a missing migration raises a clear "schema not migrated" error and writes nothing.

### 12.3 Supersession transaction (explicit administrative operation)

Approving a replacement is one atomic operation: insert `2025 / r2` as `approved` with `supersedes_publication_id` naming r1, insert its stations, observations and totals (full validation as in 12.1), then set r1 to `superseded` (with `status_changed_at` and a `status_note`), and commit all together. If r2's parse or import fails at any step, the transaction rolls back, **r1 remains `approved` and canonical**, and nothing is superseded before r2 has been validated and persisted. Admin tooling is not part of this design; the contract is.

### 12.4 Source mutation policy (final)

If the vetted URL serves changed bytes:
- ingestion fails (the pinned size/SHA-256 verification refuses the file) and **no existing publication changes**;
- a human reviews the file: if it is unexpected or corrupt it is rejected (a `rejected` record may be kept for the audit trail); if it is a legitimate replacement, it is added to the manifest as the **next revision sequence** and imported through 12.3;
- an existing revision is never silently rewritten, and revision order is never assigned from download time.

The workflow is `fail closed -> manual review -> explicitly approved revision`. Changed bytes are never treated as a new revision automatically.

## 13. Diagnostics treatment

- **Stored: published group totals** (`energy_generation_group_totals`, closed in favor of inclusion in migration 014). They are first-class published ERB values, they carry publication vintage, ERB may restate them (national totals are restated, section 3), and with dashes in station columns they cannot be recovered by summing stations. They are stored separately from station observations (52 rows for ERB 2025 r1).
- **Not stored: parser-integrity diagnostics** (footing residuals, per-row cell counts, the Table 12 / Table 16 comparison). Be precise about reproducibility:
  - *Reconstructible from stored data:* group-level footing comparisons, because stations and group totals are both stored (with dashes handled as in the parser: the sum covers reported stations only).
  - *Not reconstructible:* the Table 12 / Table 16 comparison, because Table 12 is not part of Phase 1 storage. Table 12 is deliberately not added to storage only to make this reproducible.
  - Diagnostics that are not stored remain import-time validation evidence (parser output, test evidence and import logs); storage is not required merely for reproducibility. If an audit need appears, an append-only import-run record can be added later without touching the published layer.

## 14. Query examples (design only; no migration)

Eligible publications are `approved`; accepted history uses `approved` and `superseded`.

Canonical annual generation (view body), with fallback provenance and a latest-eligible flag that does not depend on the row:

```sql
CREATE VIEW energy_generation_canonical AS
WITH eligible AS (
  SELECT * FROM energy_publications WHERE publication_status = 'approved'
), latest AS (
  SELECT DISTINCT ON (source_series) id AS latest_publication_id, source_series
  FROM eligible
  ORDER BY source_series, publication_year DESC, revision_sequence DESC
)
SELECT DISTINCT ON (o.source_station_id, o.observation_year)
       p.source_series, s.station_group, s.station_name, o.observation_year,
       o.value_state, o.generation_gwh, o.published_precision,
       o.publication_id, p.publication_year, p.revision_sequence,
       o.source_table, o.source_pdf_page, o.source_printed_page,
       (o.publication_id = l.latest_publication_id) AS is_latest_publication
FROM energy_generation_observations o
JOIN eligible p ON p.id = o.publication_id
JOIN energy_source_stations s ON s.id = o.source_station_id AND s.source_series = p.source_series
JOIN latest l ON l.source_series = p.source_series
ORDER BY o.source_station_id, o.observation_year, p.publication_year DESC, p.revision_sequence DESC;
```

Exact-vintage snapshot (no fallback, no mixing; the revision is mandatory):

```sql
SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision,
       o.source_table, o.source_pdf_page, o.source_printed_page
FROM energy_generation_observations o
JOIN energy_publications p ON p.id = o.publication_id
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE p.source_series = :source_series AND p.publication_year = :year AND p.revision_sequence = :revision
  AND p.publication_status IN ('approved', 'superseded');
```

All accepted vintages for one station and year (provenance included):

```sql
SELECT p.source_series, p.publication_year, p.revision_sequence, p.publication_status, p.title, p.source_sha256,
       o.value_state, o.generation_gwh, o.published_precision, o.source_table, o.source_pdf_page
FROM energy_generation_observations o
JOIN energy_publications p ON p.id = o.publication_id
JOIN energy_source_stations s ON s.id = o.source_station_id AND s.source_series = p.source_series
WHERE s.source_series = :source_series AND s.station_group = :group AND s.station_name = :station
  AND o.observation_year = :year AND p.publication_status IN ('approved', 'superseded')
ORDER BY p.publication_year, p.revision_sequence;
```

Revision history with classification and precision tracking:

```sql
SELECT p.publication_year, p.revision_sequence,
       o.value_state, o.generation_gwh, o.published_precision,
       lag(o.generation_gwh)       OVER w AS previous_generation_gwh,
       lag(o.published_precision)  OVER w AS previous_precision,
       (o.published_precision IS DISTINCT FROM lag(o.published_precision) OVER w
          AND lag(o.value_state) OVER w = 'reported' AND o.value_state = 'reported') AS precision_changed,
       CASE
         WHEN lag(o.value_state) OVER w IS NULL THEN 'new_observation'
         WHEN lag(o.value_state) OVER w = 'reported' AND o.value_state = 'reported'
              THEN CASE WHEN o.generation_gwh = lag(o.generation_gwh) OVER w THEN 'unchanged' ELSE 'revised' END
         WHEN lag(o.value_state) OVER w = 'reported' AND o.value_state = 'not_reported' THEN 'withdrawn'
         WHEN lag(o.value_state) OVER w = 'not_reported' AND o.value_state = 'reported' THEN 'newly_reported'
         ELSE 'still_not_reported'
       END AS change
FROM energy_generation_observations o
JOIN energy_publications p ON p.id = o.publication_id
WHERE o.source_station_id = :station_id AND o.observation_year = :year
  AND p.publication_status IN ('approved', 'superseded')
WINDOW w AS (PARTITION BY p.source_series ORDER BY p.publication_year, p.revision_sequence)
ORDER BY p.publication_year, p.revision_sequence;
```

Every ordering that involves publications uses `(publication_year, revision_sequence)`; none orders by year alone.

## 15. Future API implications (sketch, not a commitment)

- A default read such as `GET /api/v1/energy/zm/generation` would return canonical observations (the view) with `publication_year`, `revision_sequence`, `source_table`, `source_pdf_page`, `value_state`, `is_latest_publication`, `published_precision` and the value as a decimal string, `null` for `not_reported`, never `0`.
- Later and optional: an exact-vintage parameter that must name year **and** revision, and `include_revisions` attaching the section 10 history.
- Nothing is exposed externally until the rights gate (section 16) is resolved; a first read would be stored-only (like the FX monthly read) and fail closed on ambiguous data.

## 16. Rights

Every publication row carries `rights_status = 'review_required'` (ERB copyright notice, no reuse licence found, ERB not contacted). The CHECK means loosening it is an explicit migration decision. Storing data locally is **not** permission to expose it; any API, UI or export remains separately gated by a rights review. PDFs and extracted text are never stored, and parser fixtures stay synthetic.

## 17. Invariants

Database-enforced (CHECK, UNIQUE, FK, triggers):

- publication identity `UNIQUE (source_series, publication_year, revision_sequence)` (so `2025 r1` and `2025 r2` coexist); `source_sha256` is 64 lowercase hex and unique; `supersedes_publication_id` is unique, never self, and belongs to the same series and year (composite FK); revision 1 replaces nothing and later revisions name a predecessor; status in the four allowed values; rights constrained; size, pages and expected counts valid;
- every observation and total references an existing publication (and station, for observations);
- observation year in a sane range;
- `reported` implies value and precision present; `not_reported` implies both NULL; generation finite, nonnegative and under a sanity bound; stored scale equals the printed precision;
- one observation per publication, station and year; one total per publication, group and year;
- source table, PDF page and printed page are present on every observation and total;
- station name non-empty and trimmed; station group one of the four approved groups;
- no `UPDATE` or `DELETE` on stations, observations and totals; no `DELETE` on publications; on publications only `publication_status`, `status_changed_at` and `status_note` may change, through legal transitions (trigger).

Application-enforced (the importer and the review operation):

- a higher, consecutive `revision_sequence` than the revision being replaced, and the atomic supersession transaction (12.3);
- manifest-specific completeness by key set (section 8) before any write, and stored counts equal to the stored expectations before commit;
- station rows are used only with publications of the same `source_series`;
- the parser's fail-closed behavior and pinned diagnostics remain the integrity gate;
- publication metadata comes only from the code manifest; the ordinary importer never changes status.

## 18. Migration 014 plan

1. `db/init/014_energy_generation_vintages.sql`: the four tables of section 6, their constraints, the observation index, the immutability triggers (6.5), the status-transition trigger and the canonical view; idempotent, no data; applied to existing volumes by hand like 012 and 013 (init scripts run only on an empty volume).
2. Importer job (a separate task): reuse `parse_bulletin`, add the schema-not-migrated check, manifest key-set validation, one transaction, counts-only output and a `--dry-run` that never opens a connection; the supersession path is a separate explicit operation.
3. Stored-only read function and tests on a disposable `_test` PostgreSQL database: insert order, idempotent re-import, precision-only conflict (`393.40` versus `393.4`), conflict rollback, `2025 r1` plus `2025 r2` coexistence, supersession atomicity and rollback, the status-transition and immutability triggers, canonical fallback, newer-dash behavior, and snapshot exactness. The vintage logic is exercised with hypothetical synthetic publications before a second real bulletin is imported. The 2024 bulletin would be the first real second vintage; it needs its own manifest and table specs (tables numbered 21-24, different station columns).
4. Only after review and the rights decision: any API route, then any UI.

## 19. Questions

### Closed for migration 014

- **Same-year revisions and supersession:** identity `(source_series, publication_year, revision_sequence)`, `supersedes_publication_id`, atomic supersession (sections 6.1, 7.3, 12.3).
- **Publication eligibility:** `pending`, `approved`, `superseded`, `rejected`; only `approved` is canonical-eligible; the status columns are the single sanctioned mutation (section 7).
- **Insert-only enforcement:** triggers, with the narrow administrative exception (6.5).
- **Ordering and "latest":** `(publication_year, revision_sequence)` among approved publications of one series (9.1).
- **Omission and newer-dash rules** (9.2), snapshot semantics (9.3).
- **Group-totals table:** included in migration 014 (section 13).
- **Station-group behavior:** a distinct source-station identity per group (9.5).
- **Completeness:** manifest-specific key sets; stored expected counts; no global 390/52 (section 8).
- **Stable-field equality:** the field lists of section 11, including precision.
- **Source mutation:** fail closed, manual review, next revision (12.4).
- **Table 12:** not stored; the diagnostics-reproducibility statement is corrected (section 13).

### Safely deferred (do not affect migration 014)

- API presentation details (how `not_reported`, `is_latest_publication` and fallbacks are shown, and the vintage parameter shape).
- Whether Energy Sector Report values later become a separate validation series.
- A master plant registry and explicit entity linking across groups and names.
- Whether omission fallback should ever expire after N vintages (the rule stays "fall back").
- Public rights approval and any change to the `review_required` constraint.
- Admin tooling for review and supersession (only the transactional contract is fixed here).

## 20. Worked examples (hypothetical)

All values below are **hypothetical**, for illustration only. The 28 dash-to-zero cells and the `393.40` to `393.4` precision change in section 3 are real; the numbers in this section are not ERB data.

Publication `erb_statistical_bulletin / 2025 / r1` and `erb_statistical_bulletin / 2026 / r1`, station X, year 2024:

| publication | station | year | value_state | generation_gwh |
|---|---|---|---|---|
| 2025 r1 | X | 2024 | reported | 100.00 |
| 2026 r1 | X | 2024 | reported | 102.50 |

Both rows stay stored. The canonical view returns `102.50` from `2026 r1` with `is_latest_publication = true`. The history returns `2025 r1: 100.00 new_observation` then `2026 r1: 102.50 revised`.

Dash case, station Y, year 2023:

| publication | station | year | value_state | generation_gwh |
|---|---|---|---|---|
| 2025 r1 | Y | 2023 | reported | 5.00 |
| 2026 r1 | Y | 2023 | not_reported | NULL |

Canonical returns `2026 r1`: `not_reported`, `NULL` (the newer explicit statement governs). The history shows `new_observation` then `withdrawn`, and `5.00` is still queryable.

Omission case, station Z, year 2022: `2025 r1` reports `7.00`; `2026 r1` has no row for Z. Canonical returns `2025 r1`'s `7.00` with `is_latest_publication = false`; nothing is treated as deleted.

Same-year replacement: `2025 r1` reports station X, year 2024 as `100.00` and an approved `2025 r2` reports `101.00`. Both rows stay stored; r1 becomes `superseded`; canonical returns `101.00` from `2025 r2`; the history shows `revised`. If r2's import had failed, r1 would still be `approved` and canonical.

Precision-only change: `2025 r1` reports `393.40` (precision 2) and `2026 r1` reports `393.4` (precision 1). The numbers are equal, the history shows `unchanged` with `precision_changed = true` (previous precision 2, current 1), and re-importing `2026 r1` with a different printed precision than the stored row is a conflict, not a no-op.
