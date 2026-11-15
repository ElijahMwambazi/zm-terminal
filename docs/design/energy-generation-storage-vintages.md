# Energy generation: storage and publication-vintage design

Status: **design only**, drafted 2026-11-15 on branch `design/energy-generation-storage-vintages` (base `953f5e9`, the approved parser). Nothing here is implemented: no migration, SQL, store function, API route, frontend or backfill job exists. Rights for all ERB data remain `review_required` (local/internal use only).

## 1. Goals

- Store exactly what a specific ERB publication reported for a specific station and observation year, without ever replacing an earlier published value.
- Keep publication vintage, restatements and provenance queryable, so a reader can see both what ERB says now and what it said before.
- Preserve `reported` versus `not_reported` (a printed dash), never turning a dash into zero.
- Give reads a deterministic, documented "current" value that is derived, not stored by overwriting.
- Stay small, strongly typed and consistent with the repo's existing conventions (`db/init/NNN_*.sql`, idempotent `CREATE ... IF NOT EXISTS`, rows never updated, `NUMERIC` plus a published-precision column, application-level import checks).

## 2. Non-goals

- No migration, importer, store function, API route, frontend or backfill in this task.
- No master plant registry and no automatic station merging or entity resolution.
- No generic metric/EAV system.
- No storage of parser-integrity diagnostics, raw PDF bytes or extracted page text.
- No decision on public release: storage is not permission to expose data (section 16).

## 3. Source behavior (what the design must absorb)

Verified by reading the pinned 2025 bulletin and, for comparison, the downloaded 2024 bulletin and the 2024/2025 Energy Sector Reports. The cross-bulletin comparison below was a one-off reading of table text, not parser output.

| Observation | Evidence |
|---|---|
| ERB republishes history and **table numbers change per publication** | Bulletin 2024 prints the generation tables as Tables 21-24 (years 2013-2024); the pinned 2025 bulletin prints them as Tables 13-16 (2013-2025). |
| **Station sets grow between vintages** | The 2024 bulletin's IPP table has 8 station columns and total 5,848.01 GWh for 2024; the 2025 bulletin's Table 16 has 14 and prints 6,096.15 GWh for 2024. The 8 shared stations carry identical values; the total changed because columns were added (Dangote, CEC Emergency Generators, ...). Column order is also publication-specific. |
| **A dash in one vintage becomes a printed `0` in the next** | Comparing the IPP table across the two bulletins for the 8 shared stations and 2013-2024: 96 cells; 68 numerically equal, **28 dash in 2024 bulletin and `0` in 2025 bulletin**, 0 changed numbers. Example: `Bangweulu Power Limited` 2013 is `-` then `0`. |
| Same number, different printed precision | 3 cells: `393.40` (2024 bulletin) versus `393.4` (2025 bulletin). |
| Large hydro and ZESCO diesel tables: no change | Large hydro 2013-2024: 36 of 36 cells numerically equal. ZESCO diesel: 39 equal and 45 dash-to-dash, 0 changed. (The mini-hydro comparison was not completed and is not claimed.) |
| National totals are restated | National generation for 2024 is 13,324.38 GWh in the 2024 Energy Sector Report and 13,566.52 GWh in the 2025 report (the text says 13,568). |
| Same publication year, different documents | The 2025 bulletin and the 2025 Energy Sector Report report slightly different station values for 2025 (for example Kafue Gorge Mini Hydro 81.00 versus 81.04). A "vintage" is a specific document, not a year. |
| The URL is not a stable version | `esr2025.pdf` and `esr2026.pdf` are byte-identical files. |
| A dash has no published legend | ERB defines neither `-` nor `0`; the dash-to-zero changes show a dash is not a reliable "withdrawn" marker. |

## 4. Design options

**Option A, side-by-side publication vintages.** Every (publication, station, year) is its own immutable row. "Current" is a derived selection over those rows.
- Simple: one write path (insert-only), no update logic, no canonical/history divergence possible.
- Provenance is intrinsic: each row is exactly what one document said.
- Revision history is a plain query over the rows.
- Query cost is a `DISTINCT ON` or window over a few thousand rows per publication (390 per bulletin); trivial at this size.
- Future corrections: a wrong import is fixed by removing a whole publication's rows and re-importing (an explicit operational action), never by editing rows.

**Option B, canonical row plus revision-history table.** A mutable "current" table plus an append-only history.
- Duplicates every value and needs update mechanics on each import.
- Failure modes: a crash or bug between the two writes leaves canonical and history divergent; the project's idempotency and conflict-aware import patterns would have to be extended with update semantics.
- It adds an overwrite path, which is exactly the risk this project avoids (FX rows are never updated).

**Option C, event/revision model.** Each publication emits revision events relative to the previous value.
- Needs a stable ordering of events and reconstruction to read any value; classification and storage are entangled.
- Unnecessary complexity for annual data with roughly 390 observations per publication.

**Recommendation: Option A**, with the "current" value derived by a view or query rule (section 10). This agrees with the stated default preference and no analysis found a concrete reason to depart from it.

## 5. Recommended model

Two layers plus an optional totals table:

1. **Immutable published observation layer.** `energy_generation_observations`: one row = what one ERB publication reported for one source station and one observation year.
2. **Canonical/latest selection layer.** A deterministic rule (section 10) implemented as a SQL **view** first. A materialized view or an explicit selection table would be justified only if a measured read cost or a manual curation requirement appears; neither is expected at roughly 390 rows per publication. The view never mutates or deletes anything.
3. **Published group totals** (`energy_generation_group_totals`): the printed Total column per table-year, kept as published values separate from station observations (section 13).

Supporting tables: `energy_publications` (normalized publication identity) and `energy_source_stations` (publication-derived station identities). Typed, per-metric observation tables are preferred over a generic metric dimension (section 6).

## 6. Schema proposal (not a migration)

Names follow the repo's snake_case plural tables. The first migration would be `db/init/014_energy_generation_vintages.sql`, idempotent (`IF NOT EXISTS`), creating no rows.

```sql
-- energy_publications: one row per vetted source document (a vintage). Immutable.
CREATE TABLE IF NOT EXISTS energy_publications (
    id                BIGSERIAL PRIMARY KEY,
    publisher         TEXT    NOT NULL CHECK (publisher = 'ERB'),
    publication_series TEXT   NOT NULL,               -- e.g. 'erb_annual_statistical_bulletin'
    title             TEXT    NOT NULL,               -- '2025 ANNUAL STATISTICAL BULLETIN'
    publication_year  SMALLINT NOT NULL CHECK (publication_year BETWEEN 2000 AND 2100),
    cover_date        TEXT,                           -- as printed ('February 2026'); not parsed
    source_url        TEXT    NOT NULL,
    source_sha256     TEXT    NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_size_bytes BIGINT  NOT NULL CHECK (source_size_bytes > 0),
    pdf_pages         INTEGER NOT NULL CHECK (pdf_pages > 0),
    rights_status     TEXT    NOT NULL CHECK (rights_status = 'review_required'),
    first_retrieved_at TIMESTAMPTZ NOT NULL,
    imported_at       TIMESTAMPTZ NOT NULL,
    UNIQUE (source_sha256),
    UNIQUE (publication_series, publication_year)     -- one accepted document per series per year (section 12)
);

-- energy_source_stations: station identities AS PRINTED, scoped to a source series. Not a master plant registry.
CREATE TABLE IF NOT EXISTS energy_source_stations (
    id             BIGSERIAL PRIMARY KEY,
    source_series  TEXT NOT NULL,                     -- 'erb_statistical_bulletin_station_generation'
    station_group  TEXT NOT NULL CHECK (station_group IN
                     ('zesco_large_hydro','zesco_mini_hydro','zesco_diesel','independent_power_producer')),
    station_name   TEXT NOT NULL CHECK (station_name = btrim(station_name) AND station_name <> ''),
    UNIQUE (source_series, station_group, station_name)
);

-- energy_generation_observations: what one publication reported for one station and year. Insert-only.
CREATE TABLE IF NOT EXISTS energy_generation_observations (
    id                  BIGSERIAL PRIMARY KEY,
    publication_id      BIGINT   NOT NULL REFERENCES energy_publications (id),
    source_station_id   BIGINT   NOT NULL REFERENCES energy_source_stations (id),
    observation_year    SMALLINT NOT NULL CHECK (observation_year BETWEEN 1990 AND 2100),
    value_state         TEXT     NOT NULL CHECK (value_state IN ('reported','not_reported')),
    generation_gwh      NUMERIC,                      -- exact printed value; NULL only when not_reported
    published_precision INTEGER,                      -- printed decimal places; NULL only when not_reported
    source_table        TEXT     NOT NULL,            -- as printed in THIS publication ('Table 16')
    source_pdf_page     INTEGER  NOT NULL CHECK (source_pdf_page > 0),
    source_printed_page INTEGER  NOT NULL CHECK (source_printed_page > 0),
    imported_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (publication_id, source_station_id, observation_year),
    CHECK ((value_state = 'reported'     AND generation_gwh IS NOT NULL AND published_precision IS NOT NULL)
        OR (value_state = 'not_reported' AND generation_gwh IS NULL     AND published_precision IS NULL)),
    CHECK (generation_gwh IS NULL OR (generation_gwh >= 0 AND generation_gwh < 1000000
           AND generation_gwh NOT IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC, '-Infinity'::NUMERIC))),
    CHECK (generation_gwh IS NULL OR scale(generation_gwh) = published_precision)
);
CREATE INDEX IF NOT EXISTS energy_generation_obs_station_year
    ON energy_generation_observations (source_station_id, observation_year, publication_id);

-- energy_generation_group_totals: the printed Total column, kept as a published value. Not a station and not an observation.
CREATE TABLE IF NOT EXISTS energy_generation_group_totals (
    id                  BIGSERIAL PRIMARY KEY,
    publication_id      BIGINT   NOT NULL REFERENCES energy_publications (id),
    station_group       TEXT     NOT NULL CHECK (station_group IN
                          ('zesco_large_hydro','zesco_mini_hydro','zesco_diesel','independent_power_producer')),
    observation_year    SMALLINT NOT NULL CHECK (observation_year BETWEEN 1990 AND 2100),
    total_gwh           NUMERIC  NOT NULL CHECK (total_gwh >= 0 AND total_gwh < 1000000),
    published_precision INTEGER  NOT NULL CHECK (published_precision >= 0 AND scale(total_gwh) = published_precision),
    source_table        TEXT     NOT NULL,
    source_pdf_page     INTEGER  NOT NULL CHECK (source_pdf_page > 0),
    source_printed_page INTEGER  NOT NULL CHECK (source_printed_page > 0),
    imported_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (publication_id, station_group, observation_year)
);
```

Notes:
- **Metric.** The first table is deliberately `energy_generation_observations` with the unit (GWh) in the column name. Future metrics (installed capacity MW, imports/exports GWh, consumption GWh, peak demand MW) get their own typed tables that reuse `energy_publications` (and `energy_source_stations` where stations apply). This keeps strong typing and plain SQL, avoids an EAV table, and still supports expansion. A shared metric dimension is not justified until two metrics demonstrably need the same query path.
- **Numeric type.** Unconstrained `NUMERIC` (as `fx_monthly_averages` and the other repo tables do) preserves the printed scale exactly (`393.40` stays `393.40`; a typmod such as `NUMERIC(14,4)` would pad to four decimals and lose it). Observed printed precision: 0 to 2 decimals in the bulletin generation tables (`0`, `393.4`, `7463.00`), and up to 4 decimals elsewhere in ERB material (`0.6519` GWh in an Energy Sector Report diesel row). Magnitude: the largest value is about 20,000 GWh (national totals), well under the `< 1000000` guard, which is a sanity bound, not a data claim. `CHECK (scale(...) = published_precision)` makes the exact printed scale an enforced invariant. If a future metric needs a different scale, it gets its own table and column definition; nothing is shared.
- **Station identity.** `UNIQUE (source_series, station_group, station_name)` with the name exactly as printed. `Itezhi tezhi` (ZESCO diesel) and `Itezhi tezhi Power Corporation` (IPP) are distinct rows twice over (different group and different name). `source_table` is **not** part of station identity because table numbers change per publication (Tables 21-24 in the 2024 bulletin, 13-16 in 2025). A station that ERB later moves to a different group would, by design, get a new station row (an explicit entity-resolution decision, not an automatic merge).
- **Publication metadata** is normalized into `energy_publications` (about 10 stable fields per document, referenced by 390 observations per document). `source_table`, `source_pdf_page` and `source_printed_page` stay on the observation, because different observations of one document come from different tables and pages.
- **Rights.** `rights_status` is constrained to `review_required` until a rights review changes the constraint deliberately.

## 7. Publication identity

A publication is a **specific verified document**: `(publication_series, publication_year)` identifies the vintage and `source_sha256` identifies the bytes. Both are unique, which encodes the conservative policy in section 12.2: one accepted file per series per year, and no two rows may share bytes. Publisher, title, URL, size, page count and cover date are descriptive and immutable. `first_retrieved_at` records when the pinned bytes were first fetched; observation `imported_at` is import time and is excluded from equality (as for the BoZ FX tables).

`publication_series` identifies the document family (the annual bulletin versus the Energy Sector Report, which are different series and are never mixed in canonical selection). `source_series` on stations (`erb_statistical_bulletin_station_generation`) identifies the dataset and is deliberately not encoded per year; vintage lives only in the publication row, avoiding one series per year.

## 8. Observation identity

Logical uniqueness: **`UNIQUE (publication_id, source_station_id, observation_year)`**. The metric is implicit in the table (`generation_gwh`). Source table is not part of identity: given a publication, a station name appears in exactly one table of that publication (the station row already carries the group), so adding the table would only duplicate information. A row from one publication can never overwrite another publication's row, because the publication is part of the key and rows are never updated.

## 9. Missing-value semantics

| Printed cell | `value_state` | `generation_gwh` | `published_precision` |
|---|---|---|---|
| number such as `12.30` | `reported` | `12.30` | 2 |
| printed `0` | `reported` | `0` | 0 |
| dash | `not_reported` | `NULL` | `NULL` |

Enforced by the two-way CHECK in the schema (`reported` implies value and precision present; `not_reported` implies both absent). NULL alone does not carry the meaning. A dash is never stored as zero, and a printed zero is never stored as NULL. The parser's existing semantics map one-to-one onto these rows.

## 10. Vintage and canonical selection

All selection happens **within one `publication_series`** (bulletin observations never mix with Energy Sector Report observations; the latter are validation-only).

**Rule:** for each (source station, observation year), the canonical observation is the row from the publication with the greatest `publication_year` (ties impossible, see 12.2) among the publications of that series that contain **a row for that station and year**.

Answers to the specific questions:

- **Is publication year enough?** Yes for the bulletin series: it is the publisher's own edition number and `UNIQUE (publication_series, publication_year)` guarantees no ties. Publication date is not used because ERB prints only a cover month ("February 2026") that is not reliably machine-readable; it is stored as text for display.
- **Two different files with the same year?** Not accepted automatically. The unique constraint makes the second import fail, and a human decides whether it supersedes the first (section 12.2). No tie-breaking by `imported_at` or by id.
- **A newer publication omits an earlier station/year entirely.** **Fall back** to the latest earlier publication that contains it. Absence is not deletion: ERB does not state a withdrawal by omitting a row, and tables change shape between vintages for editorial reasons (section 3). The canonical row records which publication it came from, so a consumer can see the value is older than the latest publication (`is_latest_publication = false`, section 14).
- **A newer publication has a dash where an older one has a number.** The newer vintage **governs**: canonical becomes `not_reported` (NULL). Reason: the newer publication explicitly addresses that cell; falling back to the older number would show a value ERB chose not to repeat, and would make canonical output depend on cell history rather than on ERB's latest statement. This is materially different from omission, and it is made visible rather than hidden: the revision classification is `withdrawn` (section 11), the older numeric value remains stored and queryable, and the API may flag it. Because a dash has no published meaning and ERB has also turned dashes into zeros (section 3), consumers must not read `not_reported` as a claim that generation was zero or that the earlier value was wrong.
- **Newer `reported` where the older is a dash:** the newer value governs (`newly_reported`).

## 11. Restatement classification (designed, not implemented)

For the same logical observation (same source station, same year) across two vintages, ordered by publication year, compare consecutive vintages numerically (`Decimal` equality, so `393.40` equals `393.4`; precision change is recorded separately):

| Earlier | Later | Class |
|---|---|---|
| none | any | `new_observation` |
| reported `a` | reported `a` (numerically equal) | `unchanged` (with `precision_changed` when the printed scale differs) |
| reported `a` | reported `b`, `b <> a` | `revised` |
| reported | not_reported | `withdrawn` |
| not_reported | reported (including printed `0`) | `newly_reported` |
| not_reported | not_reported | `unchanged` |

Real example from section 3: the 28 IPP cells that were a dash in the 2024 bulletin and a printed `0` in the 2025 bulletin are `newly_reported`; `393.40` to `393.4` is `unchanged` with `precision_changed`. Classification is derived at read time from the stored rows (a window over vintages); it is not stored. Station-set growth and omission are not restatements: they appear as `new_observation` or as the absence of an earlier row.

## 12. Import semantics

### 12.1 Atomic, idempotent import

A future importer, following the BoZ importer pattern, would:

1. verify the pinned source bytes (size, SHA-256) against the manifest and run the parser, which must complete successfully with the 390-cell grid and its expected diagnostics, before any connection is opened;
2. in **one transaction**, check the schema exists (a `SchemaNotMigratedError` equivalent), upsert-by-identity the publication row, the station rows and then the observation and group-total rows;
3. classify each row: absent = insert; identical = already present; different = conflict;
4. on any conflict or unexpected count, roll back the whole publication and write nothing.

Identical re-import is a no-op (inserted 0, already present 390 plus the totals). A re-import whose values differ for the same publication identity is a **conflict, not a revision**. A different publication vintage coexists freely. Nothing ever updates or deletes an older publication's rows.

### 12.2 Source mutation

Because the identity is `(series, year)` plus SHA-256: if ERB serves **different bytes at the same URL**, the pinned verification fails first (the parser refuses a non-manifest hash), and the importer must not treat the new bytes as a new vintage on its own. **Conservative policy: a changed hash is a source-mutation error requiring manual review**, recorded as such, and nothing is written. Resolving it is a deliberate manifest change: either the new file is a genuinely new publication (a new series year, which inserts cleanly) or it replaces a publication for the same year, which needs an explicit, reviewed supersession (open question in section 19; the schema deliberately blocks it by default through `UNIQUE (publication_series, publication_year)`). Existing publication rows never mutate.

## 13. Diagnostics treatment

- **Store the published totals** (`energy_generation_group_totals`): they are first-class published values ERB itself restates (for example national totals) and later reads may want to show them without summing dash-laden station columns. 52 rows per bulletin (4 groups by 13 years). They are never treated as stations or observations.
- **Do not store parser-integrity diagnostics** (footing residuals, the Table 12 / Table 16 cross-check, per-row cell counts). They are reproducible from the stored rows plus totals, and their pinned expectations belong in the parser, which already fails closed. Storing them would add a second place that can disagree with the data. If an audit need appears later, an append-only `energy_import_runs` table (publication id, parser version, diagnostics JSON) can be added without touching the observation layer.

## 14. Query examples (design only)

All published vintages for one station and year:

```sql
SELECT p.publication_year, p.title, o.value_state, o.generation_gwh, o.source_table, o.source_pdf_page
FROM energy_generation_observations o
JOIN energy_publications  p ON p.id = o.publication_id
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE s.source_series = 'erb_statistical_bulletin_station_generation'
  AND s.station_group = 'independent_power_producer' AND s.station_name = :station
  AND o.observation_year = :year
ORDER BY p.publication_year;
```

Current canonical annual generation (view body):

```sql
CREATE VIEW energy_generation_canonical AS
SELECT DISTINCT ON (o.source_station_id, o.observation_year)
       s.station_group, s.station_name, o.observation_year,
       o.value_state, o.generation_gwh, o.published_precision,
       o.publication_id, p.publication_year, o.source_table, o.source_pdf_page, o.source_printed_page,
       p.publication_year = max(p.publication_year) OVER (PARTITION BY p.publication_series) AS is_latest_publication
FROM energy_generation_observations o
JOIN energy_publications p ON p.id = o.publication_id
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE p.publication_series = 'erb_annual_statistical_bulletin'
ORDER BY o.source_station_id, o.observation_year, p.publication_year DESC;
```

(`is_latest_publication` as written compares within the filtered set; the real view would compute the series maximum separately so it is unaffected by `DISTINCT ON`. Exact formulation is an implementation detail.)

Restatement history for one station and year, with the section 11 classification:

```sql
SELECT p.publication_year, o.value_state, o.generation_gwh,
       CASE
         WHEN lag(o.value_state) OVER w IS NULL THEN 'new_observation'
         WHEN o.value_state = 'reported' AND lag(o.value_state) OVER w = 'reported'
              THEN CASE WHEN o.generation_gwh = lag(o.generation_gwh) OVER w THEN 'unchanged' ELSE 'revised' END
         WHEN o.value_state = 'not_reported' AND lag(o.value_state) OVER w = 'reported' THEN 'withdrawn'
         WHEN o.value_state = 'reported' THEN 'newly_reported'
         ELSE 'unchanged'
       END AS change
FROM energy_generation_observations o
JOIN energy_publications p ON p.id = o.publication_id
WHERE o.source_station_id = :station_id AND o.observation_year = :year
WINDOW w AS (ORDER BY p.publication_year)
ORDER BY p.publication_year;
```

Latest full snapshot of a single publication (for example the newest bulletin, no fallback):

```sql
SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh
FROM energy_generation_observations o
JOIN energy_source_stations s ON s.id = o.source_station_id
WHERE o.publication_id = (SELECT id FROM energy_publications
                          WHERE publication_series = 'erb_annual_statistical_bulletin'
                          ORDER BY publication_year DESC LIMIT 1);
```

## 15. Future API implications (sketch, not a commitment)

- A default read such as `GET /api/v1/energy/zm/generation` would return canonical observations (the view), each carrying `publication_year`, `source_table`, `source_pdf_page`, `value_state`, `is_latest_publication` and the value as a decimal string, with `null` for `not_reported`, never `0`.
- Optional, later: `?vintage=<publication_year>` to return one publication's snapshot, and `?include_revisions=true` to attach the per-observation history with the section 11 classification.
- Nothing is exposed externally until the rights gate in section 16 is resolved; the first read, if built, would be stored-only (like the FX monthly read) and fail closed on ambiguous data.

## 16. Rights

Every publication row carries `rights_status = 'review_required'` (ERB copyright notice, no reuse licence found, ERB not contacted). The CHECK means loosening it is an explicit migration decision, not a data change. Storing data locally is **not** permission to expose it: any API, UI or export remains separately gated by a rights review, and the PDFs and extracted text are never stored. Parser fixtures stay synthetic.

## 17. Invariants

Database-enforced (CHECK, UNIQUE, FK):

- publication identity: `source_sha256` is 64 lowercase hex and unique; `(publication_series, publication_year)` is unique; size and pages positive; rights constrained;
- every observation references an existing publication and an existing source station (FK);
- observation year within a sane range;
- `reported` implies value and precision present; `not_reported` implies both NULL;
- generation is finite and nonnegative, under a sanity bound;
- stored scale equals the printed precision (`scale(value) = published_precision`);
- one observation per publication, station and year;
- source table, PDF page and printed page are present on every observation;
- station name is non-empty and trimmed; group is one of the four approved groups.

Application-enforced (like the repo's other importers):

- **publication SHA immutability and no cross-vintage overwrite**: rows are insert-only; the importer never issues UPDATE or DELETE on published rows (a trigger rejecting UPDATE/DELETE is an optional hardening, to be decided at migration time since the repo's existing tables rely on application discipline);
- complete-grid check (390 observations, 52 group totals) before any write;
- parser fail-closed behavior and the pinned diagnostics remain the integrity gate;
- publication metadata comes only from the code manifest.

## 18. Migration implementation plan

1. `db/init/014_energy_generation_vintages.sql`: the four tables, the observation index and the canonical view, idempotent, no data; applied to existing volumes by hand like 012/013 (init scripts run only on an empty volume).
2. Importer job (separate task) `python -m app.jobs.import_erb_generation [--dry-run]`: reuse `parse_bulletin`, add a `SchemaNotMigratedError`-style check, one transaction, counts-only output, `--dry-run` never touching PostgreSQL.
3. Stored-only read function and tests on a disposable `_test` PostgreSQL database (insert order, idempotency, conflict rollback, two-vintage coexistence, the canonical view, dash/zero CHECKs).
4. Only after review and the rights decision: any API route, then any UI.

Test plan for the vintage logic uses hypothetical synthetic publications (section 20) so the canonical rule is exercised before a second real bulletin is imported; the 2024 bulletin would be the first real second vintage (it needs its own manifest and parser table specs because its tables are numbered 21-24 and its station columns differ).

## 19. Open questions

1. Is a deliberate supersession of an already-imported publication (a corrected file for the same year) ever allowed, and if so does it need an explicit `edition` column or a `superseded_by` link instead of a hard unique key?
2. Add `energy_publications.status` (approved/quarantined) or keep approval implicit in the code manifest (the recommendation here)?
3. Enforce insert-only with triggers, or keep application-level discipline as the existing tables do?
4. Should canonical fall back across a much older vintage (for example a 2019 value when 2024 and 2025 both omit the station), or expire fallbacks after N vintages?
5. Whether `not_reported` from a newer vintage should be shown as the canonical state (recommended) or as "no current value" in the API.
6. Whether Energy Sector Report values enter later as a separate validation series with their own table or only as import-time checks.
7. Group changes (a station printed under a different group in a later vintage): a new station row by default; who resolves entity links, and with what evidence?
8. Whether the `energy_generation_group_totals` table belongs in the first migration or a follow-up (it is cheap and not recoverable from stations alone because of dashes, which is why it is proposed now).

## 20. Worked examples (hypothetical)

All values below are **hypothetical**, for illustration only. The 28 dash-to-zero cells and the `393.40` to `393.4` precision change in section 3 are real; the numbers in this section are not ERB data.

Publication A (year 2025) and B (year 2026) both contain station X, year 2024:

| publication | station | year | value_state | generation_gwh |
|---|---|---|---|---|
| A (2025) | X | 2024 | reported | 100.00 |
| B (2026) | X | 2024 | reported | 102.50 |

Both rows remain stored (different `publication_id`, same station and year). The canonical view returns B's `102.50` with `publication_year = 2026` and `is_latest_publication = true`. The restatement history returns `2025: 100.00 new_observation` then `2026: 102.50 revised`.

Dash case, station Y, year 2023:

| publication | station | year | value_state | generation_gwh |
|---|---|---|---|---|
| A (2025) | Y | 2023 | reported | 5.00 |
| B (2026) | Y | 2023 | not_reported | NULL |

Both rows stay. Canonical returns B: `not_reported`, `NULL` (the newer explicit statement governs), the history shows `new_observation` then `withdrawn`, and A's `5.00` is still queryable.

Omission case, station Z, year 2022: A reports `7.00`; B has no row for Z. Canonical returns A's `7.00` with `publication_year = 2025` and `is_latest_publication = false`; nothing is treated as deleted.
