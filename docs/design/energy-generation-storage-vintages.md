# Energy generation: storage and publication-vintage design

Status: **design only** (revision 3, 2026-11-16, branch `design/energy-generation-storage-vintages`, parser base `953f5e9`). Nothing here is implemented: no migration, SQL, store function, API route, frontend or backfill job exists, and the design is under review. Rights for all ERB data remain `review_required` (local/internal use only). Revision 3 separates three lifecycles that earlier revisions fused into one state machine: what ERB published, which source candidates were reviewed, and what ZM Terminal imported (section 5).

## 1. Goals

- Store exactly what a specific accepted ERB publication revision reported for a specific station and observation year, without ever replacing a published value.
- Keep publication vintage, same-year corrected files, restatements and provenance queryable.
- Preserve `reported` versus `not_reported` (a printed dash); never turn a dash into zero.
- Give reads a deterministic, documented "current" value that is derived, not stored by overwriting.
- Make a committed-but-wrong ZM Terminal import repairable with a full audit trail and without inventing an ERB publication revision.
- Stay small, strongly typed and consistent with the repo's conventions (`db/init/NNN_*.sql`, idempotent `CREATE ... IF NOT EXISTS`, `NUMERIC` plus a published-precision column, conflict-aware atomic imports).

## 2. Non-goals

- No migration, importer, store function, API route, frontend or backfill in this task.
- No master plant registry and no automatic station merging or entity resolution.
- No generic metric/EAV system.
- No storage of raw PDF bytes, extracted page text or parser-integrity diagnostics (section 14).
- No physical-purge workflow (section 8.5).
- Storage is not permission to expose data (section 17).

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

**Option A, side-by-side immutable data.** Every (import, station, year) is its own immutable row. "Current" is a derived selection over those rows.
- Simple: insert-only source-derived data, no update logic for published values, no canonical/history divergence possible.
- Provenance is intrinsic: each row is exactly what one import of one document said.
- Revision history is a plain query over the rows.
- Query cost is a `DISTINCT ON` or window over a few thousand rows per publication; trivial at this size.
- A wrong import is repaired by a new import revision of the same publication (section 13.5), never by editing or deleting rows.

**Option B, canonical row plus revision-history table.** A mutable "current" table plus append-only history. Duplicates every value, needs update mechanics on each import, and a failure between the two writes leaves canonical and history divergent. It adds exactly the overwrite path this project avoids.

**Option C, event/revision model.** Each publication emits revision events relative to the previous value. Entangles classification with storage and needs reconstruction to read any value; unnecessary for annual data with a few hundred observations per publication.

**Recommendation: Option A**, with the "current" value derived by a view (section 10).

## 5. Three lifecycles and two revision axes

These are different concepts and deliberately **do not share a state machine**:

| Lifecycle | Question it answers | Table | Statuses |
|---|---|---|---|
| **Source candidate** | Which files did we inspect before accepting any? | `energy_publication_candidates` | `pending`, `rejected`, `accepted` |
| **Accepted publication revision** | What did ERB officially publish? | `energy_publications` | `approved`, `superseded` |
| **Import revision** | How did ZM Terminal interpret that exact document? | `energy_generation_imports` | `current`, `superseded` |

Two independent revision axes:

- **Publication revision** (`revision_sequence`): ERB's official vintages. Assigned only to accepted source documents. r1, r2, r3 name ERB replacements.
- **Import revision** (`import_sequence`): local ZM Terminal audit history for one accepted publication. 1, 2, 3 name successive successful interpretations of the same bytes.

The two numbers are never interchangeable and neither orders the other: official ordering across vintages uses `(publication_year, revision_sequence)` only; `import_sequence` only decides which interpretation of one publication is active.

Terminology used throughout: **source candidate** (a file under review), **accepted publication revision** (an official ERB vintage ZM Terminal accepted), **import revision** (one successful local ingestion). The bare word "revision" is avoided where it could mean any of them. A rejected source candidate is not a "rejected publication", and a bad import does not reject the ERB document.

Layers:

1. **Review layer** (administrative): `energy_publication_candidates`. No observations, no totals.
2. **Immutable published layer**: `energy_publications`, `energy_source_stations`, and per import `energy_generation_observations` and `energy_generation_group_totals`; `energy_generation_imports` links data to a publication.
3. **Derived canonical layer**: SQL views (`energy_publication_current_imports`, `energy_generation_canonical`). A materialized view or a selection table is justified only by a measured read cost or a manual-curation need; neither is expected. Views never mutate or delete anything.
4. **Snapshot reads**: an exact publication revision (and optionally an exact import revision), never mixing vintages (section 10.3).

Typed per-metric tables are preferred over a generic metric dimension (section 7.7).

## 6. Provenance chain

Observations and totals reference an **import**; provenance is reached through `observation -> import -> publication`. Publication metadata (publication id, source series, publication year, `revision_sequence`, `source_sha256`, URL, title) is **not duplicated** on observations: the FK chain preserves it, and the views join it. Source table, PDF page and printed page stay on each observation and total because they are cell-level facts.

## 7. Schema proposal (concept, not a migration)

The first migration would be `db/init/014_energy_generation_vintages.sql`, idempotent (`IF NOT EXISTS`), creating no rows. This section lists columns and constraints; it is deliberately not SQL.

### 7.1 `energy_publication_candidates` (source review log)

Administrative review evidence for files inspected before they become accepted publication revisions. **It contains no observations or totals**, and a candidate has no `revision_sequence`.

| Column | Type / constraint | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `source_series` | TEXT NOT NULL | same domain as publications |
| `nominal_publication_year` | SMALLINT NOT NULL, 2000..2100 | the edition year the file claims |
| `source_url` | TEXT NOT NULL | |
| `source_sha256` | TEXT NOT NULL, CHECK 64 lowercase hex | |
| `source_size_bytes` | BIGINT NOT NULL, CHECK > 0 | |
| `discovered_at` | TIMESTAMPTZ NOT NULL | when the file was retrieved/inspected |
| `review_status` | TEXT NOT NULL, CHECK IN ('pending','rejected','accepted') | |
| `review_note` | TEXT NULL | required unless `pending` |
| `reviewed_at` | TIMESTAMPTZ NULL | required unless `pending` |
| `accepted_publication_id` | BIGINT NULL, FK `energy_publications` | set only when `accepted` |

Constraints: `UNIQUE (source_sha256)` (the same bytes are reviewed once); `UNIQUE (accepted_publication_id)`; `CHECK ((review_status = 'accepted') = (accepted_publication_id IS NOT NULL))`; `CHECK (review_status = 'pending' OR (review_note IS NOT NULL AND reviewed_at IS NOT NULL))`.

Permitted review-status changes (trigger): `pending -> rejected`, `pending -> accepted`, and `rejected -> accepted` (a review decision later judged wrong, with a new `review_note`). `accepted` is terminal and `rejected -> pending` is not allowed. Only `review_status`, `review_note`, `reviewed_at` and `accepted_publication_id` may change; the source identity columns are immutable. A rejected candidate:

- consumes no revision number and never enters the publication chain;
- never blocks a later candidate (a later candidate has a different SHA and is a new row);
- is never canonical-eligible, because it is not a publication row.

Where candidates are recorded: only by an explicit review operation, never by the ordinary importer (which fails closed and writes nothing, section 13.7). Candidate storage is in the database, in the Energy schema, because rejected-source evidence is part of the audit story and a small table is cheaper than an out-of-band log.

### 7.2 `energy_publications` (accepted publication revisions only)

One row per source document accepted as an official ERB publication revision. A row exists only after source review accepted the file **and** its first successful import committed (section 13.1); there are no `pending` or `rejected` publication rows.

| Column | Type / constraint | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `publisher` | TEXT NOT NULL, CHECK = 'ERB' | |
| `source_series` | TEXT NOT NULL | document family, e.g. `erb_statistical_bulletin`; never contains a year or revision |
| `publication_year` | SMALLINT NOT NULL, 2000..2100 | the edition year |
| `revision_sequence` | INTEGER NOT NULL, CHECK >= 1 | 1 = first accepted source document for the year; 2, 3, ... = accepted official replacements |
| `publication_status` | TEXT NOT NULL, CHECK IN ('approved','superseded') | administrative |
| `supersedes_publication_id` | BIGINT NULL, FK `energy_publications` | the accepted publication revision this one replaces |
| `title` | TEXT NOT NULL | as printed |
| `cover_date` | TEXT NULL | as printed ("February 2026"); not parsed |
| `source_url` | TEXT NOT NULL | |
| `source_sha256` | TEXT NOT NULL, CHECK 64 lowercase hex | |
| `source_size_bytes` | BIGINT NOT NULL, CHECK > 0 | |
| `pdf_pages` | INTEGER NOT NULL, CHECK > 0 | |
| `rights_status` | TEXT NOT NULL, CHECK = 'review_required' | loosening is a deliberate migration |
| `first_retrieved_at` | TIMESTAMPTZ NOT NULL | operational |
| `accepted_at` | TIMESTAMPTZ NOT NULL | operational |
| `status_changed_at` | TIMESTAMPTZ NOT NULL | administrative |
| `status_note` | TEXT NULL | administrative |

Constraints:
- `UNIQUE (source_series, publication_year, revision_sequence)`: the official publication identity. `2025 r1` and `2025 r2` coexist permanently; nothing is unique on `(source_series, publication_year)` alone.
- `UNIQUE (source_sha256)`: the same bytes appear in exactly one publication row. A corrected local import of the same file does **not** need another publication row; it adds an import row (section 7.3), so the SHA is reused through `many imports -> one publication` and never duplicated across publications.
- `UNIQUE (supersedes_publication_id)`: an accepted publication revision is replaced by at most one.
- `UNIQUE (id, source_series, publication_year)` and a composite FK `(supersedes_publication_id, source_series, publication_year)` referencing it: a replacement belongs to the same series and the same nominal year (skipped when `supersedes_publication_id` is NULL).
- `CHECK ((revision_sequence = 1) = (supersedes_publication_id IS NULL))`; `CHECK (supersedes_publication_id <> id)`.
- **Cross-row lifecycle invariants are enforced by database triggers (section 18):** the predecessor must have `revision_sequence = current - 1` in the same series and year (consecutive, no gaps); within a series and year only the highest revision may be `approved` and every lower one must be `superseded` (checked at commit); a publication must have at least one import at commit; only the transition `approved -> superseded` is legal; only the status columns may change.

### 7.3 `energy_generation_imports` (local ingestion revisions)

One row per **completed ZM Terminal import of one accepted publication**. A transaction that never commits leaves no row.

| Column | Type / constraint | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `publication_id` | BIGINT NOT NULL, FK `energy_publications` | |
| `import_sequence` | INTEGER NOT NULL, CHECK >= 1 | 1 = first successful import; 2, 3, ... = reviewed corrections |
| `import_status` | TEXT NOT NULL, CHECK IN ('current','superseded') | |
| `expected_observation_count` | INTEGER NOT NULL, CHECK >= 0 | manifest expectation this import was validated against (section 9) |
| `expected_group_total_count` | INTEGER NOT NULL, CHECK >= 0 | same |
| `importer_version` | TEXT NOT NULL | code identity that produced the import (audit only) |
| `correction_reason` | TEXT NULL | required when `import_sequence > 1` |
| `imported_at` | TIMESTAMPTZ NOT NULL | operational |
| `status_changed_at` | TIMESTAMPTZ NOT NULL | administrative |
| `status_note` | TEXT NULL | administrative |

Constraints:
- `UNIQUE (publication_id, import_sequence)`.
- At most one `current` import per publication, and (at commit) exactly one: a unique partial index on `(publication_id) WHERE import_status = 'current'`, made deferrable where PostgreSQL allows (a deferrable exclusion constraint on `publication_id` for `current` rows) so a correction can insert and demote inside one transaction; plus a deferred constraint trigger that every publication has exactly one `current` import at commit. The mechanism is a migration detail; the invariant is not.
- `CHECK ((import_sequence = 1) = (correction_reason IS NULL))`.
- Trigger: `import_sequence` is consecutive for its publication (`n` follows `n - 1`); the only legal transition is `current -> superseded`; only the status columns may change.

Expected counts live on the import, not on the publication, because the manifest is part of what a correction may fix; a corrected import is validated against the corrected manifest and records what it was validated against.

### 7.4 `energy_source_stations`

Station identities exactly as printed, scoped to a source series. Not a master plant registry.

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `source_series` | TEXT NOT NULL (same value domain as publications) |
| `station_group` | TEXT NOT NULL, CHECK IN ('zesco_large_hydro','zesco_mini_hydro','zesco_diesel','independent_power_producer') |
| `station_name` | TEXT NOT NULL, CHECK non-empty and trimmed |

`UNIQUE (source_series, station_group, station_name)`. Table number is not part of the identity because table numbers change between vintages. Stations are shared across imports and publications and are immutable.

### 7.5 `energy_generation_observations`

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `import_id` | BIGINT NOT NULL, FK `energy_generation_imports` |
| `source_station_id` | BIGINT NOT NULL, FK `energy_source_stations` |
| `observation_year` | SMALLINT NOT NULL, 1990..2100 |
| `value_state` | TEXT NOT NULL, CHECK IN ('reported','not_reported') |
| `generation_gwh` | NUMERIC NULL |
| `published_precision` | INTEGER NULL |
| `source_table` | TEXT NOT NULL (as printed in this publication, e.g. `Table 16`) |
| `source_pdf_page` | INTEGER NOT NULL, > 0 |
| `source_printed_page` | INTEGER NOT NULL, > 0 |
| `imported_at` | TIMESTAMPTZ NOT NULL (operational) |

Constraints: `UNIQUE (import_id, source_station_id, observation_year)`; `reported` requires `generation_gwh` and `published_precision` present, `not_reported` requires both NULL; `generation_gwh` finite, `>= 0` and `< 1000000`; `scale(generation_gwh) = published_precision`. Trigger: the station's `source_series` equals the import's publication series (cross-row, so a trigger). Index on `(source_station_id, observation_year, import_id)`.

### 7.6 `energy_generation_group_totals`

The printed Total column per table-year, kept as a first-class published value and never as a station or observation.

| Column | Constraint |
|---|---|
| `id` | BIGSERIAL PK |
| `import_id` | FK `energy_generation_imports` |
| `station_group` | same four values as stations |
| `observation_year` | 1990..2100 |
| `total_gwh` | NUMERIC NOT NULL, finite, `>= 0`, `< 1000000` |
| `published_precision` | INTEGER NOT NULL, `scale(total_gwh) = published_precision` |
| `source_table`, `source_pdf_page`, `source_printed_page` | as for observations |
| `imported_at` | operational |

`UNIQUE (import_id, station_group, observation_year)`. Totals are included in migration 014 (section 14).

### 7.7 Notes

- **Metric.** The first table is `energy_generation_observations` with the unit in the column name. Future metrics (installed capacity MW, imports and exports GWh, consumption GWh, peak demand MW) get their own typed tables that reuse `energy_publications` and `energy_generation_imports` (and stations where they apply). No EAV, no shared metric dimension until two metrics demonstrably share a query path.
- **Numeric type.** Unconstrained `NUMERIC`, like the FX and other repo tables, preserves printed scale (`393.40` stays `393.40`; a typmod such as `NUMERIC(14,4)` would pad to four decimals and lose it). Observed printed precision: 0 to 2 decimals in the bulletin generation tables, up to 4 elsewhere in ERB material (`0.6519` in an Energy Sector Report diesel row). Magnitudes reach about 20,000 GWh (national totals), far under the sanity bound. `scale(value) = published_precision` makes the printed scale an enforced invariant.
- **Source series.** One value (`erb_statistical_bulletin`) names the document family on publications, candidates and stations; vintage lives only in the publication row (year plus revision), so there is never one series per year or per revision. The Energy Sector Report is a different series and is never mixed into bulletin canonical selection.

## 8. Lifecycles and immutability

### 8.1 Source candidate

`pending -> rejected | accepted`, `rejected -> accepted` (section 7.1). Administrative record: the review-status columns are the only mutable ones.

### 8.2 Accepted publication revision

`approved -> superseded` is the only mutation, and only inside the atomic acceptance of its replacement (section 13.3). `superseded` is terminal and there is no un-supersede. Source-derived metadata (everything except `publication_status`, `status_changed_at`, `status_note`) is immutable. There is no `rejected` state: refusing a file happens at the candidate level, before any publication row exists.

`approved` is canonical-eligible. `superseded` stays queryable as history and exact snapshot but is not canonical-eligible.

### 8.3 Import revision

`current -> superseded` is the only mutation, and only inside the atomic corrected import (section 13.5). Stable identity and metadata are immutable. A superseded import stays fully queryable for audit; it is never called "rejected" because the underlying ERB document may be perfectly valid.

### 8.4 Source-derived data

Stations, observations and group totals are immutable: no `UPDATE`, no `DELETE`.

### 8.5 Delete policy

Normal application behavior never deletes accepted publications, successful imports, observations, totals or source stations used by history, and migration 014 builds no delete workflow. Triggers reject `DELETE` on all energy tables. Physical purge for exceptional administration, privacy or legal reasons is outside Phase 1.

### 8.6 Administrative-mutation summary

| Table | Mutable columns | Only legal change |
|---|---|---|
| `energy_publication_candidates` | review status, note, reviewed time, accepted publication id | `pending -> rejected/accepted`, `rejected -> accepted` |
| `energy_publications` | `publication_status`, `status_changed_at`, `status_note` | `approved -> superseded` |
| `energy_generation_imports` | `import_status`, `status_changed_at`, `status_note` | `current -> superseded` |
| stations, observations, totals | none | none |

These are the only deliberate exceptions in an otherwise insert-only model. They go beyond the repo's usual application-level discipline and are justified by the audit requirement.

## 9. Publication-specific completeness

Completeness is **manifest-specific**. Nothing in the database hard-codes 390 observations or 52 group totals; those figures are the exact requirements of the pinned **ERB 2025 bulletin** only. Earlier or later bulletins may legitimately have fewer or more stations, different year coverage, different table numbers and different counts (for example the 2024 bulletin has 8 IPP columns and covers 2013-2024).

Each approved source manifest (in code) defines its expected publication shape: the expected set of source-station identities (`station_group`, `station_name`), the expected year coverage, therefore the expected observation key set and count, and the expected group-total key set and count.

For ERB 2025 bulletin r1:
- 30 source-station identities (3 large hydro, 6 mini hydro, 7 diesel, 14 IPP columns);
- years 2013-2025;
- observation keys: 30 stations x 13 years = **390**;
- group-total keys: 4 groups x 13 years = **52**.

**Every successful import, including a corrected import, must satisfy the exact manifest for its source publication.** A correction cannot bypass completeness.

**Counts are diagnostics, not proof.** Before persistence the importer must compare the parsed **logical key sets** with the manifest's expected key sets: observations keyed by `(station_group, station_name, observation_year)` and totals keyed by `(station_group, observation_year)`. A set comparison, not a count comparison, is required, so a duplicate-plus-missing pair cannot pass because the total count happens to match.

**Stored audit expectations.** Each import stores `expected_observation_count` and `expected_group_total_count` (390 and 52 for ERB 2025 r1 import 1); the full expected key sets stay in the vetted manifest/code. A deferred constraint trigger asserts, at commit, that the stored row counts of the import equal its stored expectations (database-authoritative, section 18). Migration 014 contains no global count constraint.

## 10. Canonical selection, ordering and snapshots

### 10.1 Two selection steps

1. **Publication step (official source vintage):** among publications with `publication_status = 'approved'` in one `source_series`, order by `(publication_year DESC, revision_sequence DESC)`. Superseded ERB source revisions stay historical. Source candidates never participate; they are not publication rows.
2. **Import step (active interpretation):** within each publication, only its `current` import supplies data. A superseded import never appears in normal reads.

Canonical station selection therefore operates over **approved publication + current import**. The local `import_sequence` never affects the official ordering across vintages; it only picks which interpretation of one publication is active.

### 10.2 Canonical rule

The unique identity makes the ordering total; `imported_at`, `accepted_at`, retrieval time and any import time are never tie-breakers.

**Latest eligible publication** has one meaning: the approved publication with the greatest `(publication_year, revision_sequence)` in the same `source_series`, computed independently of whether any particular station or year exists in it. The result field is `is_latest_publication`; it is never "latest publication containing this observation".

For each `(source_series, source_station_identity, observation_year)`, select the row from the greatest approved `(publication_year, revision_sequence)` whose current import **actually contains that observation**:

- **Newer approved publication omits the station/year entirely:** fall back to the latest older approved publication that contains it. Absence is not deletion; ERB does not state a withdrawal by omission and its tables change shape between vintages. The canonical row reports the older publication and `is_latest_publication = false`.
- **Newer approved publication explicitly has a dash:** the newer row wins; canonical is `value_state = not_reported`, `generation_gwh = NULL`. There is **no fallback to the older number**, because the newer publication explicitly addresses that cell. The older value stays stored and queryable, the history classifies the change as `withdrawn` (a technical label, not a claim about ERB's intent), and consumers must not read `not_reported` as zero or as proof the earlier value was wrong (ERB has no dash legend and has also turned dashes into zeros, section 3).
- **Newer approved publication reports a value where the older had a dash:** the newer value governs (`newly_reported`).
- **Same-year replacement:** approved r2 wins over r1; superseded r1 is not canonical-eligible but remains in history.

### 10.3 Snapshots

Two levels, both exact and with **no omission fallback and no mixing with another publication**; both include explicit `not_reported` rows:

- **Source-publication snapshot:** one accepted publication revision by `(source_series, publication_year, revision_sequence)`, for example `erb_statistical_bulletin / 2025 / r2`, returning the rows of that publication's **current import** by default. Available for `approved` and `superseded` publications (a superseded publication is still a historical ERB publication). A future `?vintage=` parameter must map to an exact publication revision, never to a bare year when several exist.
- **Audited import snapshot** (debugging/audit): `(publication revision, import_sequence)`, which reproduces exactly what an older, superseded ZM Terminal import of that document stored. Storage supports it; no API is designed now.

### 10.4 Canonical view versus snapshot

The canonical view is **per-observation** selection and may mix vintages (omission fallback). A canonical set is therefore **not necessarily equivalent to any single ERB bulletin**; consumers who need exactly what one bulletin printed use a snapshot. The canonical view always reports its provenance (publication id, import id, year, `revision_sequence`, `import_sequence`, `is_latest_publication`) so fallbacks are never hidden.

### 10.5 Station-group vintage behavior (closed for Phase 1)

Station identity is `(source_series, station_group, station_name)`. If a later publication prints the same station name under a different group, that is a **distinct source-station identity**, preserving ERB's source classification history; no entity linking is attempted. Canonical observation sets can therefore contain identities from historical group classifications until a future explicit entity-resolution layer exists. This is not a migration blocker.

## 11. Restatement classification (designed, derived at read time)

Three different events must never be confused:

| Event | Example | What it is |
|---|---|---|
| Local import correction | ERB 2025 r1, import 1 to import 2 after a parser bug | a ZM Terminal ingestion correction; **not** a source restatement |
| Official same-year source revision | ERB 2025 r1 to ERB 2025 r2 | an official source revision |
| Cross-edition change | ERB 2025 to ERB 2026 | a publication-vintage restatement |

The restatement history of a logical observation `(source_station_id, observation_year)` compares **accepted publication revisions** (`approved` and `superseded`; source candidates never appear), ordered by `(publication_year, revision_sequence)`, each using its **current import**. It never compares import revisions of one publication with each other, so a local correction can never be classified as an ERB restatement. History includes superseded publications deliberately: an r1 to r2 correction is exactly a restatement a reader wants to see. Canonical selection, by contrast, uses `approved` only. Consecutive vintages are compared numerically (`Decimal` equality), and precision separately:

| Earlier | Later | Class |
|---|---|---|
| none | any | `new_observation` |
| reported `a` | reported equal to `a` | `unchanged`, with `precision_changed = true` when the printed precision differs |
| reported `a` | reported `b`, `b <> a` | `revised` |
| reported | not_reported | `withdrawn` (technical label only) |
| not_reported | reported (including printed `0`) | `newly_reported` |
| not_reported | not_reported | `still_not_reported` |

For every comparison the history exposes the previous value, the previous precision, the current precision and `precision_changed`. Real examples (section 3): the 28 IPP cells that were a dash in the 2024 bulletin and a printed `0` in the 2025 bulletin are `newly_reported`; `393.40` to `393.4` is `unchanged` with `precision_changed = true`. Station-set growth and omission are not restatements.

## 12. Stable-field equality contract

"Identical" means every **stable, source-derived field** matches; it is not numeric equality alone. Operational and administrative fields never take part. The contract decides whether an ordinary re-import is a **no-op** (identical) or a **conflict requiring review** (different).

### 12.1 Publication (looked up by `source_series`, `publication_year`, `revision_sequence`)

Compared exactly: `publisher`, `source_series`, `publication_year`, `revision_sequence`, `title`, `cover_date`, `source_url`, `source_sha256`, `source_size_bytes`, `pdf_pages`, `rights_status`, and the identity of the publication named by `supersedes_publication_id` (compared as that publication's `(source_series, year, revision)`; immutable once set). Any difference under the same identity, or the same `source_sha256` appearing under a different identity, is a **conflict**.

### 12.2 Source station

`source_series`, `station_group`, exact `station_name`. No normalization, case-folding or punctuation merging; table number is not part of the identity. A "different" station is simply a different identity.

### 12.3 Import dataset (compared against the publication's `current` import)

- `expected_observation_count` and `expected_group_total_count` (the manifest expectations).
- **Observations**, matched by `(station identity, observation_year)`: `value_state`, `generation_gwh`, `published_precision`, `source_table`, `source_pdf_page`, `source_printed_page` must match exactly, and the key sets must be equal (no extra, no missing rows). `Decimal("393.40")` and `Decimal("393.4")` are numerically equal but **not identical published observations** because `published_precision` differs (2 versus 1), so a precision difference is a difference. NULL equals NULL for `not_reported` rows.
- **Group totals**, matched by `(station_group, observation_year)`: `total_gwh`, `published_precision`, `source_table`, `source_pdf_page`, `source_printed_page`, with equal key sets.

### 12.4 Excluded from equality

`first_retrieved_at`, `discovered_at`, `accepted_at`, `imported_at`, transaction timestamps, any last-checked timestamp, `status_changed_at`, `status_note`, `publication_status`, `import_status`, `importer_version`, `correction_reason`, `import_sequence` and ids. Re-running the exact same publication later, with a different retrieval time, after an administrative status change or with a different importer build that produces the same data, must remain a complete no-op.

## 13. Workflows

All future behavior; no admin tooling or importer exists. The contracts are fixed here; database invariants in section 18 are authoritative.

### 13.1 First import of an accepted publication (atomic)

Rule adopted: **an accepted publication record, its first `current` import, its observations and totals, and the candidate's `accepted` marking are committed atomically.** A publication row never exists without at least one valid successful import (a deferred constraint trigger enforces it), so `energy_publications` needs no `pending` state; pre-ingestion audit state lives in the candidate log.

1. Source review: verify size and SHA-256 against the vetted manifest entry; the human decision to accept is recorded in the manifest. The candidate stays `pending` until commit.
2. Parse; the parser must succeed.
3. Validate manifest completeness by key set (section 9) and parser diagnostics against their pinned expectations.
4. Begin one transaction.
5. Assign `revision_sequence` (1, or the highest accepted revision for the series and year plus one) under a per-(series, year) advisory lock; the trigger re-verifies it.
6. Insert the publication as `approved` (with `supersedes_publication_id` when `revision_sequence > 1`), insert source stations, insert import 1 as `current` with expected counts, insert observations and totals.
7. Mark the candidate `accepted` with `accepted_publication_id` (the trigger checks series, nominal year, URL, SHA and size agree with the publication).
8. If this is a replacement, mark the predecessor `superseded` (section 13.3).
9. Commit. Deferred triggers verify stored counts, exactly one current import, and one approved publication per series-year.

### 13.2 Ordinary failed import: rollback only

If parsing, validation, completeness checking, diagnostics or any insertion fails before commit, roll back: no partial observations, no partial totals, no import row, no publication row and no candidate acceptance. A failed attempt is not a committed bad import, so no `superseded` or other import row is created for something that never successfully existed. Operational logs may record the failure separately.

### 13.3 Official source replacement (atomic)

For a legitimate ERB replacement of an accepted publication:

1. A candidate for the new file is reviewed and accepted by decision (the candidate row stays `pending` until commit).
2. Parse and validate the new file as in 13.1.
3. Begin the transaction; insert the new publication revision (`revision_sequence = predecessor + 1`, `supersedes_publication_id = predecessor`), its first `current` import with all observations and totals.
4. Mark the predecessor `superseded` (with `status_changed_at` and `status_note`); mark the candidate `accepted`.
5. Commit.

If any step fails, everything rolls back and **the predecessor remains `approved` and canonical**. Nothing is superseded before the replacement is fully validated and persisted. Rejected candidates are outside this chain and consume no revision number. Example: 2025 r1 is approved; ERB's URL later serves different bytes; candidate A is reviewed and rejected and remains only in candidate history; candidate B is later reviewed and accepted and becomes **2025 r2**, superseding r1. There is no rejected r2.

### 13.4 Idempotent re-import and conflicting output

Ordinary ingestion of an already accepted publication:

- The source bytes must equal the publication's `source_sha256`; the publication is looked up by identity and compared under section 12.1.
- The new parse is compared against the publication's **`current` import** under section 12.3.
- **Identical:** return a no-op. No new import sequence, no writes to observations or totals. Re-running a command never creates import 2.
- **Different** (the same publication and SHA produce different stable output): **fail closed**. The ordinary path never creates an import revision, so a parser regression cannot silently become the new truth. The workflow is `difference detected -> review -> approved import correction -> new import_sequence` (section 13.5).

### 13.5 Corrected import of the same accepted publication (atomic)

Scenario: ERB 2025 r1's bytes are valid, import 1 committed, and a parser/storage bug is later found to have stored wrong observations.

1. Leave the ERB publication row unchanged.
2. Leave import 1 and its observations and totals unchanged, for audit.
3. Fix the parser/storage logic.
4. Parse the **same source bytes** again (same SHA, same publication).
5. Validate manifest completeness (section 9) and diagnostics. A corrected import cannot bypass either.
6. Record the review decision (the `correction_reason`) approving it as an implementation correction.
7. Begin one transaction; insert import 2 (`import_sequence = 2`, `correction_reason` set) with all of its observations and totals; mark import 1 `superseded`; import 2 is `current`.
8. Commit.

On any failure the transaction rolls back and import 1 remains `current` and untouched; at no point does a publication lack a current import (the deferred triggers enforce exactly one at commit). From then on canonical reads and the default snapshot for that publication use import 2; import 1 remains reachable through the audited import snapshot.

Forbidden: a fake ERB r2, a different SHA, updating import 1 observations, deleting import 1, or rewriting source publication metadata.

### 13.6 Import history versus official history

The corrected import changes no official ordering and no restatement classification (section 11). A reader who sees `import_sequence = 2` knows only that ZM Terminal re-interpreted the same document.

### 13.7 Source mutation policy (final)

If the vetted URL serves changed bytes:
- ingestion fails (size/SHA-256 verification refuses the file), writes nothing (including no candidate row; recording is an explicit review operation) and **no existing publication or import changes**;
- a human records the file as a **source candidate** and reviews it: unexpected or corrupt files are `rejected` (the candidate row is the audit evidence and it consumes no revision number); a legitimate replacement is added to the manifest and accepted through 13.3 as the **next publication revision**;
- an existing publication is never silently rewritten, and revision order is never assigned from download time.

The workflow is `fail closed -> candidate review -> explicitly accepted publication revision`.

### 13.8 Schema precondition

As in the BoZ importer, a missing migration raises a clear "schema not migrated" error and writes nothing.

## 14. Diagnostics treatment

- **Stored: published group totals** (`energy_generation_group_totals`, closed in favor of inclusion in migration 014). They are first-class published ERB values, they carry publication vintage, ERB may restate them (national totals are restated, section 3), and with dashes in station columns they cannot be recovered by summing stations. They are stored separately from station observations (52 rows for ERB 2025 r1 import 1).
- **Not stored: parser-integrity diagnostics** (footing residuals, per-row cell counts, the Table 12 / Table 16 comparison). Be precise about reproducibility:
  - *Reconstructible from stored data:* group-level footing comparisons, because stations and group totals are both stored (with dashes handled as in the parser: the sum covers reported stations only).
  - *Not reconstructible:* the Table 12 / Table 16 comparison, because Table 12 is not part of Phase 1 storage. Table 12 is deliberately not added to storage only to make this reproducible.
  - Diagnostics that are not stored remain import-time validation evidence (parser output, test evidence and import logs); storage is not required merely for reproducibility. The import row records `importer_version` and the expected counts; if a richer audit need appears, an append-only import-run record can be added later without touching the published layer.

## 15. Query examples (design only; no migration)

Each example states which lifecycle it reads. Every production read of accepted data requires `publication_status = 'approved'` **and** `import_status = 'current'` unless it is explicitly a history or audit query.

Helper view (accepted publications with their active import):

```sql
CREATE VIEW energy_publication_current_imports AS
SELECT p.id AS publication_id, p.source_series, p.publication_year, p.revision_sequence,
       p.publication_status, p.title, p.source_sha256,
       i.id AS import_id, i.import_sequence
FROM energy_publications p
JOIN energy_generation_imports i ON i.publication_id = p.id AND i.import_status = 'current';
```

**Candidate review history** (administrative only):

```sql
SELECT c.source_series, c.nominal_publication_year, c.source_url, c.source_sha256, c.discovered_at,
       c.review_status, c.review_note, c.reviewed_at, c.accepted_publication_id
FROM energy_publication_candidates c
WHERE c.source_series = :source_series
ORDER BY c.discovered_at;
```

**Official publication history** (accepted publications only; no candidates):

```sql
SELECT p.publication_year, p.revision_sequence, p.publication_status, p.supersedes_publication_id,
       p.title, p.source_sha256, p.accepted_at
FROM energy_publications p
WHERE p.source_series = :source_series
ORDER BY p.publication_year, p.revision_sequence;
```

**Import history** (successful local ingestions of one publication):

```sql
SELECT p.publication_year, p.revision_sequence, i.import_sequence, i.import_status,
       i.correction_reason, i.importer_version, i.imported_at
FROM energy_generation_imports i
JOIN energy_publications p ON p.id = i.publication_id
WHERE p.source_series = :source_series AND p.publication_year = :year AND p.revision_sequence = :revision
ORDER BY i.import_sequence;
```

**Canonical annual generation** (view body: approved publication + current import, with fallback provenance and a latest-eligible flag that does not depend on the row):

```sql
CREATE VIEW energy_generation_canonical AS
WITH eligible AS (
  SELECT * FROM energy_publication_current_imports WHERE publication_status = 'approved'
), latest AS (
  SELECT DISTINCT ON (source_series) publication_id AS latest_publication_id, source_series
  FROM eligible
  ORDER BY source_series, publication_year DESC, revision_sequence DESC
)
SELECT DISTINCT ON (o.source_station_id, o.observation_year)
       e.source_series, s.station_group, s.station_name, o.observation_year,
       o.value_state, o.generation_gwh, o.published_precision,
       e.publication_id, e.publication_year, e.revision_sequence, e.import_id, e.import_sequence,
       o.source_table, o.source_pdf_page, o.source_printed_page,
       (e.publication_id = l.latest_publication_id) AS is_latest_publication
FROM energy_generation_observations o
JOIN eligible e ON e.import_id = o.import_id
JOIN energy_source_stations s ON s.id = o.source_station_id AND s.source_series = e.source_series
JOIN latest l ON l.source_series = e.source_series
ORDER BY o.source_station_id, o.observation_year, e.publication_year DESC, e.revision_sequence DESC;
```

**Current exact source-publication snapshot** (no fallback, no mixing; the publication revision is mandatory; current import by default):

```sql
SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision,
       o.source_table, o.source_pdf_page, o.source_printed_page
FROM energy_publication_current_imports e
JOIN energy_generation_observations o ON o.import_id = e.import_id
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE e.source_series = :source_series AND e.publication_year = :year AND e.revision_sequence = :revision;
```

**Old import audit snapshot** (reproduces a superseded local import exactly):

```sql
SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision,
       o.source_table, o.source_pdf_page, o.source_printed_page
FROM energy_generation_imports i
JOIN energy_publications p ON p.id = i.publication_id
JOIN energy_generation_observations o ON o.import_id = i.id
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE p.source_series = :source_series AND p.publication_year = :year AND p.revision_sequence = :revision
  AND i.import_sequence = :import_sequence;
```

**All accepted vintages for one station and year** (provenance included; uses each publication's current import):

```sql
SELECT e.source_series, e.publication_year, e.revision_sequence, e.publication_status, e.import_sequence, e.title, e.source_sha256,
       o.value_state, o.generation_gwh, o.published_precision, o.source_table, o.source_pdf_page
FROM energy_publication_current_imports e
JOIN energy_generation_observations o ON o.import_id = e.import_id
JOIN energy_source_stations s ON s.id = o.source_station_id AND s.source_series = e.source_series
WHERE s.source_series = :source_series AND s.station_group = :group AND s.station_name = :station
  AND o.observation_year = :year
ORDER BY e.publication_year, e.revision_sequence;
```

**Official restatement history** with classification and precision tracking (accepted publication revisions, each through its current import; local import corrections do not appear):

```sql
SELECT e.publication_year, e.revision_sequence,
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
FROM energy_publication_current_imports e
JOIN energy_generation_observations o ON o.import_id = e.import_id
WHERE o.source_station_id = :station_id AND o.observation_year = :year
WINDOW w AS (PARTITION BY e.source_series ORDER BY e.publication_year, e.revision_sequence)
ORDER BY e.publication_year, e.revision_sequence;
```

Every ordering that involves publications uses `(publication_year, revision_sequence)`; none orders by year alone and none orders by `import_sequence`.

## 16. Future API implications (sketch, not a commitment)

- A default read such as `GET /api/v1/energy/zm/generation` would return canonical observations (the view) with `publication_year`, `revision_sequence`, `source_table`, `source_pdf_page`, `value_state`, `is_latest_publication`, `published_precision` and the value as a decimal string, `null` for `not_reported`, never `0`. The import sequence is an audit field and need not be exposed by default.
- Later and optional: an exact-vintage parameter that must name year **and** publication revision, and `include_revisions` attaching the section 11 history.
- Nothing is exposed externally until the rights gate (section 17) is resolved; a first read would be stored-only (like the FX monthly read) and fail closed on ambiguous data.

## 17. Rights

Every publication row carries `rights_status = 'review_required'` (ERB copyright notice, no reuse licence found, ERB not contacted). The CHECK means loosening it is an explicit migration decision. **Candidate acceptance, accepted-publication status (`approved`) and current-import status (`current`) are all data-quality/workflow approvals, not copyright or reuse permission.** Storing data locally is not permission to expose it; any API, UI or export remains separately gated by a rights review. PDFs and extracted text are never stored, and parser fixtures stay synthetic.

## 18. Enforcement responsibility and invariants

**Principle:** application validation provides early, descriptive errors; **database constraints and triggers are authoritative** for every invariant PostgreSQL can enforce. Application checks are duplicated for usability, but the database is the final authority for the lifecycle invariants below. No lifecycle rule is "application only" unless PostgreSQL cannot express it.

Application layer (early, descriptive; prevalidates before any write):
- manifest completeness by key set (section 9) and expected-count agreement;
- parser diagnostics against their pinned expectations;
- the candidate review workflow and manifest approval;
- stable-dataset comparison: identical (no-op) versus conflicting (fail closed);
- the correction-approval record (`correction_reason`).

Database constraints (declarative):
- uniqueness: publication identity `(source_series, publication_year, revision_sequence)`; `source_sha256` on publications and candidates; `supersedes_publication_id`; `(publication_id, import_sequence)`; observation `(import_id, source_station_id, observation_year)`; total `(import_id, station_group, observation_year)`; station identity;
- foreign keys: observation and total to import, import to publication, observation to station, candidate to accepted publication, composite same-series/same-year supersession FK;
- value and state checks: `reported` implies value and precision, `not_reported` implies both NULL; generation finite, nonnegative, under the sanity bound; `scale = published_precision`; year ranges; statuses in their allowed sets; rights constrained; non-empty trimmed station names; 64-hex hashes; source table and pages present;
- at most one `current` import per publication; revision 1 has no predecessor and later revisions name one.

Database triggers (cross-row lifecycle invariants that CHECK/FK cannot express):
- accepted publication revision sequence and predecessor: consecutive, same series and year, `n` replaces `n - 1`;
- legal status transitions: publication `approved -> superseded` only; import `current -> superseded` only; candidate `pending -> rejected/accepted`, `rejected -> accepted`;
- only the administrative columns may change on candidates, publications and imports;
- at commit (deferred): within a series and year only the highest publication revision is `approved`; every publication has exactly one `current` import; stored observation and total counts equal the import's stored expectations; a candidate marked `accepted` agrees with its publication on series, year, URL, SHA and size;
- `import_sequence` is consecutive per publication;
- the station's series equals the publication's series;
- immutability: `UPDATE`/`DELETE` rejected on stations, observations and totals; `DELETE` rejected everywhere.

Not expressible in PostgreSQL, so application-only: that the parsed key sets equal the vetted manifest's key sets (the manifest lives in code), that diagnostics match their pinned values, and that the stable dataset is identical to the current import.

## 19. Migration 014 plan

1. `db/init/014_energy_generation_vintages.sql`: `energy_publication_candidates`, `energy_publications`, `energy_generation_imports`, `energy_source_stations`, `energy_generation_observations`, `energy_generation_group_totals`, the observation index, the constraints and triggers of section 18, and the views `energy_publication_current_imports` and `energy_generation_canonical`; idempotent, no data; applied to existing volumes by hand like 012 and 013 (init scripts run only on an empty volume).
2. Importer job (a separate task): reuse `parse_bulletin`, add the schema-not-migrated check, manifest key-set validation, one transaction, counts-only output and a `--dry-run` that never opens a connection. First import, source replacement and import correction are separate explicit operations.
3. Stored-only read function and tests on a disposable `_test` PostgreSQL database: first-import atomicity, idempotent re-import, same-SHA conflict fail-closed, precision-only difference (`393.40` versus `393.4`), `2025 r1` plus `2025 r2` coexistence, rejected-candidate-then-accepted-candidate (no revision gap), source-replacement atomicity and rollback, corrected-import atomicity and rollback (old import stays current on failure), exactly-one-current at commit, the trigger and immutability rules, canonical fallback, newer-dash behavior, and snapshot exactness for both snapshot levels. The vintage logic is exercised with hypothetical synthetic publications before a second real bulletin is imported. The 2024 bulletin would be the first real second vintage; it needs its own manifest and table specs (tables numbered 21-24, different station columns).
4. Only after review and the rights decision: any API route, then any UI.

## 20. Questions

### Closed for migration 014

- **Source candidates versus accepted publications:** separate tables and state machines; candidates carry no revision number (sections 5, 7.1, 7.2).
- **Accepted publication statuses:** `approved`, `superseded`; only `approved` is canonical-eligible (8.2).
- **Import revisions:** `energy_generation_imports` with `current`/`superseded`; observations and totals belong to an import (7.3, 7.5, 7.6).
- **Official revision identity:** `(source_series, publication_year, revision_sequence)`; local identity `(publication_id, import_sequence)` (7.2, 7.3).
- **Recovery from a committed bad import:** a corrected import of the same bytes, never a fake ERB revision (13.5).
- **Atomicity:** first import, source replacement and corrected import (13.1, 13.3, 13.5); failed attempts roll back (13.2).
- **Idempotent re-import and conflicting output** (13.4).
- **Enforcement responsibility:** database authoritative, application early (18).
- **Insert-only enforcement and the narrow administrative exceptions** (8.6).
- **Ordering and "latest":** `(publication_year, revision_sequence)` among approved publications of one series, current import only (10.1, 10.2).
- **Omission and newer-dash rules** (10.2), snapshot semantics (10.3).
- **Group-totals table:** included in migration 014 (14).
- **Station-group behavior:** a distinct source-station identity per group (10.5).
- **Completeness:** manifest-specific key sets; stored expected counts per import; no global 390/52 (9).
- **Stable-field equality** (12), **source mutation** (13.7), **Table 12 not stored** (14).

### Safely deferred (do not affect migration 014)

- API presentation details (how `not_reported`, `is_latest_publication` and fallbacks are shown, and the vintage parameter shape).
- Whether Energy Sector Report values later become a separate validation series.
- A master plant registry and explicit entity linking across groups and names.
- Whether omission fallback should ever expire after N vintages (the rule stays "fall back").
- Public rights approval and any change to the `review_required` constraint.
- Admin tooling for candidate review, source replacement and import correction (only the transactional contracts are fixed here).
- Exceptional physical purge (privacy/legal), outside Phase 1.

## 21. Worked examples (hypothetical)

All values below are **hypothetical**, for illustration only. The 28 dash-to-zero cells and the `393.40` to `393.4` precision change in section 3 are real; the numbers in this section are not ERB data.

Publications `erb_statistical_bulletin / 2025 / r1` and `erb_statistical_bulletin / 2026 / r1` (each through its current import), station X, year 2024:

| publication | station | year | value_state | generation_gwh |
|---|---|---|---|---|
| 2025 r1 | X | 2024 | reported | 100.00 |
| 2026 r1 | X | 2024 | reported | 102.50 |

Both rows stay stored. The canonical view returns `102.50` from `2026 r1` with `is_latest_publication = true`. The restatement history returns `2025 r1: 100.00 new_observation` then `2026 r1: 102.50 revised`.

Dash case, station Y, year 2023: `2025 r1` reports `5.00`; `2026 r1` has a dash. Canonical returns `2026 r1`: `not_reported`, `NULL` (the newer explicit statement governs). The history shows `new_observation` then `withdrawn`, and `5.00` is still queryable.

Omission case, station Z, year 2022: `2025 r1` reports `7.00`; `2026 r1` has no row for Z. Canonical returns `2025 r1`'s `7.00` with `is_latest_publication = false`; nothing is treated as deleted.

Same-year official replacement: `2025 r1` reports station X, year 2024 as `100.00` and an accepted `2025 r2` reports `101.00`. Both stay stored; r1 becomes `superseded`; canonical returns `101.00` from `2025 r2`; the restatement history shows `revised`. If r2's first import had failed, r1 would still be `approved` and canonical.

Rejected candidate, then accepted candidate: `2025 r1` is approved. ERB's URL changes; candidate A (SHA a) is reviewed and rejected and stays only in candidate history. Candidate B (SHA b) is later accepted and becomes `2025 r2`, superseding r1. The publication chain is r1, r2 with no gap and no rejected r2.

Committed bad import: `2025 r1`, import 1 stored a station value from the wrong column. The parser is fixed and the same bytes re-parsed. Import 2 is written and import 1 is marked `superseded` in one transaction. `2025 r1` is unchanged and still `approved`; canonical and the default snapshot use import 2; the audited import snapshot of import 1 still shows the wrong value; the restatement history shows nothing, because no ERB publication changed.

Precision-only change: `2025 r1` reports `393.40` (precision 2) and `2026 r1` reports `393.4` (precision 1). The numbers are equal, the history shows `unchanged` with `precision_changed = true` (previous precision 2, current 1), and re-importing `2026 r1` with a different printed precision than its current import is a conflict, not a no-op.
