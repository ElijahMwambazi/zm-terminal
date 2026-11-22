# Zambia CPI Phase 1: publication manifest and parser foundation

Status: **implemented foundation, no storage, no import, no API, no UI.** Rights: `review_required`, local/internal use only.
Normative source contract: `docs/research/zambia-cpi-inflation.md`. This document records what the committed manifest proves so the
manifest can be reviewed without re-reading the research. Code: `apps/api/app/services/zamstats_cpi_{models,table,source,combine}.py`.

## 1. Scope

Exactly seven national series on the `2009=100` regime: CPI index (All Items, Food, Non-Food), monthly inflation (All Items, Food,
Non-Food) and annual inflation (All Items only). Boundaries (implementation boundaries, not claims that earlier rows do not exist):
index from 2010-01, monthly inflation from 2011-01, annual inflation from 2011-01. Through 2026-10 (Vol 283, the newest bulletin
when the manifest was vetted). Deferred: annual Food/Non-Food, the old `1994=100` regime, divisions, provinces, contributions,
derived or analytical series.

## 2. Architecture

```text
manifest (committed pins) -> verify bytes (size, %PDF, SHA-256) -> extract ONLY the pinned pages (pdftotext -layout, no OCR)
   -> pure layout parsers (one family per table shape) -> immutable source observations (evidence)
   -> reconcile -> resolved logical observations + conflicts (fail closed)
```

No runtime archive crawling, no URL construction, no "newest PDF" lookup, no BoZ call, no database, no migration. Published rates
are authoritative: nothing is calculated, repaired, rounded or forward-filled.

## 3. The manifest: 14 publications, 2012-12 to 2026-10

All files are `https://www.zamstats.gov.zm/wp-content/uploads/...` PDFs. Each entry pins URL, byte size, SHA-256, archive label,
the legible PDF cover, layout families, tables (page, printed month extent, canonical window) and the pages to extract. All 14 were
downloaded from the official public URLs, hash-checked and parsed with the real extractor (see section 8).

| key | bulletin (archive label) | tables pinned (PDF page, family) | canonical windows |
|---|---|---|---|
| `zam-cpi-2012-12` | Vol 117, Dec 2012 (cover: Volume 116) | index p23 (`vertical_labelled`, prefixed, duplicate Dec 2010), monthly p5 (`quoted_months`) | index 2010-01..2012-11; monthly 2011-01..2012-12 |
| `zam-cpi-2014-12` | Vol 140, Dec 2014 | monthly p5 (`vertical_labelled`) | monthly 2013-01..2014-12 |
| `zam-cpi-2015-12` | Vol 152, Dec 2015 | index p24 (`vertical_labelled`), monthly p5 (`horizontal_13`, no weight column) | index 2012-12..2015-12; monthly 2015 |
| `zam-cpi-2016-12` | Vol 164, Dec 2016 | index p29, annual p31 (`annual_rows`), monthly p9 (`horizontal_13`) | index 2016; annual 2011-01..2016-12; monthly 2016 |
| `zam-cpi-2018-01` | Vol 177, Jan 2018 | monthly p6 (`horizontal_13`, wrapped Non-Food tail of 5) | monthly 2017 |
| `zam-cpi-2018-12` | Vol 189, Dec 2018 | monthly p7 (`horizontal_13`, heading printed twice) | monthly 2018 |
| `zam-cpi-2019-12` | Vol 201, Dec 2019 | annual p32 (`annual_rows`), monthly p10 (`horizontal_13`) | annual 2017-01..2019-12; monthly 2019 |
| `zam-cpi-2020-12` | Vol 213, Dec 2020 | index p42 (Annex 1.1), monthly p8 (Table 5) | index 2017-01..2020-12; monthly 2020 |
| `zam-cpi-2021-12` | Vol 225, Dec 2021 | monthly p12 (Table 7) | monthly 2021 |
| `zam-cpi-2022-12` | Vol 237, Dec 2022 | monthly p13 (Table 8) | monthly 2022 |
| `zam-cpi-2023-12` | Vol 249, Dec 2023 | index p50, annual p52 (`annual_rows`), monthly p12 | index 2021-01..2023-12; annual 2020-01..2023-12; monthly 2023 |
| `zam-cpi-2024-12` | Vol 261, Dec 2024 | monthly p13 (Table 7) | monthly 2024 |
| `zam-cpi-2025-12` | Vol 273, Dec 2025 | index p25, annual p27 (`annual_rows`), monthly p12 | index 2024-01..2025-12; annual 2024-2025; monthly 2025 |
| `zam-cpi-2026-10` (`latest`) | Vol 283, Oct 2026 | index p23, annual p25 (`vertical_labelled`), monthly p9 (wrapped Non-Food tail of 4) | index/annual/monthly 2026-01..2026-10 |

Why these bulletins: monthly rates are published only for a trailing 12 to 13 months per bulletin, so one year-end bulletin per year
(or the January 2018 bulletin for 2017, whose December 2017 table is the wrapped layout) is the smallest set that proves every month
of 2011-2026. The index and annual tables span three to five years each, so a sparser subset of the same bulletins owns them.
Other 2012-2016 and 2017-2019 candidates were rejected because their tables did not parse unambiguously (see section 7).

## 4. Canonical-window rule

**Each table declares the exact months and columns it may canonically supply (`canonical_first`/`canonical_last`).** Everything else
the table prints is validation evidence only. No "newest wins", no "earliest wins". The coverage proof (`manifest_problems` /
`validate_manifest`) demands, for each of the seven series, exactly one canonical owner for every month from its boundary to
2026-10, and reports (by series, month range and key) missing months, doubly claimed months, pre-boundary canonical rows, unsupported
series, wrong regime, bad URL/hash pins, an unpinned base-evidence page and a missing or duplicated `latest` publication.

Result for the committed manifest: **no problems; 1,366 logical observations each have one pinned owner** (3 x 202 index, 3 x 190
monthly, 190 annual). The annual table's `Annual CPI` column is validation-only evidence; the Food/Non-Food index table owns the All
Items index.

## 5. Layout families (positively identified, no fuzzy matching)

- `vertical_labelled`: one row per month, 2 or 3 value columns; the year is a label on its own line inside its 12-month block or a
  prefix on one row of the block. Exactly one label per block, labels consecutive, months contiguous, first/last month pinned.
- `annual_rows`: `Year Month Annual CPI Annual Inflation Rate`, the year on every row.
- `quoted_months`: `Mon 'YY - Mon 'YY` rows (month-on-month); the compared month must be the preceding month.
- `horizontal_13`: 12 or 13 month columns with Total/Food/Non-Food rows pinned by their fixed weights (1,000, 534.85, 465.15) or, in
  one layout, no weights; header month names are validated, **header years are not used** (real headers contain typos such as
  `May-2024` inside a 2025 window). Two explicit wrapped variants print the last four or five Non-Food values on the `Non-` line
  above the weight line.

Every table needs its title anchor to match exactly the pinned number of lines (normally 1; 2 for one bulletin that prints its heading
twice), prints no base other than `2009 = 100`, and prints the base itself or relies on a pinned base-evidence page of the same PDF.

## 6. Numeric grammar and sign metadata

Per-family lexical contract (observed conventions, not global invariants): index values 1 to 3 digits, `.` and 1 or 2 decimals
(real tables drop trailing zeros in places); rates 1 decimal in `x.y`, `-x.y` or `(x.y)`. Rejected: `+`, bare `.5`, exponents,
Unicode minus, extra decimals, thousands separators, dashes and `N/A`. Every number keeps `source_value_text`, a `Decimal`,
`source_negative` and `source_notation` (`plain`/`minus`/`parentheses`). `(0.0)` parses to `Decimal("0.0")` with
`source_negative = true`; the evidence lives in explicit fields, not in the numeric value.

## 7. Source conflicts and anomalies found while vetting (all preserved, none resolved)

**Ten logical observations have disagreeing source cells** (reconciliation reports them; the foundation chooses nothing):

| series | month | kind | note |
|---|---|---|---|
| `index_all_items` | 2013-02, 2014-02, 2014-08 | same publication (Vol 164) | Food/Non-Food index table vs annual table differ by 0.02 to 0.06 in every bulletin that prints both |
| `index_all_items` | 2022-10 | same publication (Vol 249 and Vol 273) | Annex 1.1 vs Annex 1.3 differ by 0.03 |
| `index_all_items` | 2017-03, 2018-05, 2019-02 | across publications | annual table (Dec 2019) vs Annex 1.1 (Dec 2020) |
| `index_food` | 2023-08 | across publications | Dec 2023 bulletin differs from the Dec 2025 and Oct 2026 bulletins (a later restatement) |
| `monthly_inflation_all_items` | 2014-12 | across publications | Dec 2014 bulletin vs Dec 2015 bulletin |
| `monthly_inflation_all_items` | 2022-12 | across publications | Dec 2022 bulletin vs Dec 2023 bulletin |

Eight further observations are **formatting-only** overlaps (numerically equal, source text different, for example `274.20` vs
`274.2`); both strings are kept as evidence and these are not conflicts. Other anomalies recorded in the manifest `notes`: the
archive lists two months as "Vol 121" (outside this manifest); Vol 117's PDF cover reads Volume 116; Vol 117's index table prints
December 2010 twice in a row (collapsed only under an explicit per-table authorisation, and reported); Vol 249's Annex 1.1 caption says
2018-2023 but its rows start in January 2020 and page 52 carries a stale `Volume 247` running header; Vol 201's Table 6 title says
October 2018 to October 2019 while its columns are December 2018 to December 2019; Vol 273's monthly header prints May-2024; several
covers have no text layer. Candidates **not** used because their tables did not parse unambiguously: Vol 128 index (a duplicated row
with no authorisation) and the Vol 176 monthly table (wrapped continuation lines in a different arrangement; replaced by Vol 177).

## 8. Verification performed

- All 14 PDFs were fetched sequentially from their official URLs (low volume, no Cloudflare-protected host, no archive crawl), SHA-256
  and size verified against the manifest, and every pinned table parsed through the repository's `pdf_layout_text` adapter.
- The optional gate `tests/test_zamstats_cpi_real_source.py` (env var `ZM_ZAMSTATS_CPI_PDF_DIR`, no network, skipped by default)
  re-checks every publication, known edge values (October 2026; a printed `(0.0)`) and the full reconciliation: 1,356 resolved, 10
  conflicts (the table above), 8 formatting variants.
- The normal suite is offline and uses small synthetic fixtures (`tests/fixtures/zamstats_cpi_*.txt`, invented numbers). No PDF is
  committed.

## 9. Fail-closed behaviour

Zero or multiple title matches, a missing base, another base period, a gap or duplicate month, unexpected first/last month,
unauthorised duplicate rows, wrong year-label counts, unknown notation, wrong weight rows, a hash/size/magic mismatch, a redirect, a
bad status, an oversized body, a missing text layer and any old-regime evidence all raise `ZamstatsCpiError` with a message naming
tables, pages, months, series or keys (never source values). `require_resolved` raises on any conflict.

## 10. What comes next (not in this slice)

Storage (four conceptual families: publications, source observations, canonical index and inflation observations), the importer, the
read API and the UI, each after review of this foundation. The ten conflicts need a reviewed resolution rule before any of those
observations can be canonical. No real CPI data has been imported anywhere.
