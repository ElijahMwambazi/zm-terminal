# Zambia CPI and inflation: source discovery (research only)

Status: **research only**. Nothing here is implemented. No parser, migration, table, route, frontend component or existing dataset was changed, and no data was imported. Research date: 2026-11-22; revised after review the same day. Base: `main` at `fd48c5f` (PR #41, Bank FX frontend).

## 1. Executive recommendation

- **Canonical publisher: Zambia Statistics Agency (ZamStats)**, via its **Monthly Bulletin** PDFs (text layer, no OCR needed in the files read). The official archive lists **283 bulletin PDFs from Vol 1 (April 2003) through Vol 283 (October 2026)**; **only a representative subset (32 of 283) was inspected** for content and layout. Nothing here implies archive-wide completeness or layout stability.
- **No usable canonical machine-readable ZamStats CPI source was found during this research.** The WordPress REST API is public but carries no CPI data. The ZamStats data-portal hosts returned a Cloudflare JavaScript challenge (not bypassed) or did not answer. The data source is therefore PDF text extraction.
- **Current regime: index reference period `2009 = 100`.** The headline series moved to it with the January 2012 bulletin; the earlier headline used a different `1994 = 100` regime (section 5). **No later rebasing was found in the sampled 2014 to 2026 bulletins reviewed**; weights were identical in every sampled bulletin from 2012 to 2026. A rebasing outside the sample remains possible.
- **Bank of Zambia (BoZ) is a secondary cross-check only.** Its JSON feed is clean but republishes ZamStats imperfectly (section 12).
- **Rights: `review_required`**, local/internal use only (section 13).
- **Decision: APPROVED FOR LOCAL/INTERNAL PHASE 1 IMPLEMENTATION**, `rights_status = review_required`, narrowed to **seven source-published national series on the `2009 = 100` regime** (section 14), **conditional on a vetted publication manifest and layout-family contract before any controlled real import** (section 17). Annual Food and Non-Food inflation, divisions, provincial data, the old regime and all derived analytics are **deferred**.

## 1a. Verification status

**Verified by reading downloaded PDF text layers (`pdftotext -layout`):** 32 of 283 bulletins: Vol 1 (Apr 2003), 9, 21, 33, 45, 57 (Dec 2003 to Dec 2007), 69 (Dec 2008), 81 (Dec 2009), 93 (Dec 2010), 94 to 97 (Jan to Apr 2011), 105 (Dec 2011), 106 (Jan 2012), 117 (Dec 2012), 121 (Apr and May 2013, covers only), 129 (Jan 2014), 133 (May 2014), 141 (Jan 2015), 153 (Jan 2016), 164 (Dec 2016), 165 (Jan 2017), 177 (Jan 2018), 201 (Dec 2019), 213 (Dec 2020), 225 (Dec 2021), 249 (Dec 2023), 261 (Dec 2024), 271 and 273 (Oct and Dec 2025), 282 and 283 (Sep and Oct 2026). Vol 283 was read in full for the CPI sections. BoZ's inflation feed was downloaded in full (200 rows) and compared programmatically.

**Not verified:** the 251 other bulletins; continuous monthly coverage of any metric across the archive; most numeric rates before 2008; the Statistics Act No. 13 of 2018 and any formal terms of use; whether the retrospective tables of section 5 share methodology lineage with the current regime.

## 2. Method and limits

- ZamStats pages and PDFs were fetched with plain anonymous `curl` and a research User-Agent, sequentially with a one-second pause. `robots.txt` disallows only `/wp-admin/`.
- The `*.opendataforafrica.org` portals returned a Cloudflare "Just a moment… Enable JavaScript and cookies" page (HTTP 403). This was **not** worked around.
- BoZ's website is a single-page application; its public bundle names the same Drupal JSON:API base already used by this project's BoZ importers. The inflation view was read anonymously.
- Throwaway parsers used for comparisons lived in the session scratchpad. No parser, PDF or table content is committed; figures below are quoted only to justify source decisions.
- Every claim cites a bulletin volume and table so it can be re-checked.

## 3. Official sources and exact URLs

| # | Publisher | Resource | URL | Format | Verdict |
|---|---|---|---|---|---|
| 1 | ZamStats | Monthly Bulletin archive page | `https://www.zamstats.gov.zm/monthly-bulletin/` | HTML; 473 PDF `href`s (the page repeats its list blocks) resolving to 283 distinct URLs, Vol 1 (Apr 2003) to Vol 283 (Oct 2026) | **canonical discovery index** |
| 2 | ZamStats | Latest bulletin | `https://www.zamstats.gov.zm/wp-content/uploads/2026/10/Vol-283-of-2026-The-Monthly-October.pdf` (2.4 MB, 59 pages, text layer, PDF created 29 Oct 2026, `Last-Modified` 29 Oct 2026 12:57 GMT) | PDF | **canonical data** |
| 3 | ZamStats | WordPress REST root | `https://www.zamstats.gov.zm/wp-json/` | JSON | public, only WordPress/SEO/Elementor routes; no CPI dataset |
| 4 | ZamStats | Data portals | `nso-zambia.opendataforafrica.org`, `zambiainfohighway.opendataforafrica.org` (Cloudflare challenge), `zamb.opendataforafrica.org`, `nada.zamstats.gov.zm` (no answer) | n/a | **not usable**; access control not bypassed |
| 5 | ZamStats | FAQ | `https://www.zamstats.gov.zm/faqs/` | HTML | rights evidence (section 13) |
| 6 | BoZ | Inflation view | `https://www.boz.zm/api/v1/views/inflation_rates` | JSON, 200 rows | **cross-check only** |

Bulletin URL pattern: `.../wp-content/uploads/{YYYY}/{MM}/Vol-{N}-of-{YYYY}-The-Monthly-{Month}.pdf`, but file names are irregular (`...-Flnal.pdf`, `...-241225.pdf`, `Vol_276-of-...`, older ones under `/2023/12/`), so URLs must come from the archive page, never be guessed.

**Archive numbering is not reliable as an identity.** The archive lists `Vol-120-2013-The-Monthly-March`, `Vol-121-2013-The-Monthly-April` and `Vol-121-2013-The-Monthly-May`: **two different months carry the label "Vol 121"**, and the April PDF's own cover reads "Volume 120" (the May cover reads "Volume 121"; the March cover was not checked). Discovery must therefore keep distinct URLs, files, hashes and archive labels and must never discard a publication because its displayed volume number repeats (section 17).

## 4. Current publication contract (Volume 283, October 2026)

Each bulletin has a narrative CPI section and a CPI annex (Annex 1, Tables 1.1 to 1.4). The measures are distinct:

| Item | What it is | Where | Window in Vol 283 |
|---|---|---|---|
| All Items / Food / Non-Food CPI | **index level**, `2009 = 100` | Annex Table 1.1 | Jan 2023 to Oct 2026 (46 months) |
| All Items CPI by 12 divisions | index level | Annex Table 1.2 | same |
| CPI index and annual inflation, all items | index level plus **annual percentage change** | Annex Table 1.3 | Jan 2022 to Oct 2026 (58 months) |
| CPI by province | index level, 9 province columns | Annex Table 1.4 | Jan 2023 to Oct 2026 |
| Annual inflation by 12 main groups | annual percentage change | Table 1 | trailing 13 months |
| Contribution of main groups; provincial contribution | percentage-point contribution (ZamStats-derived) | Tables 2 and 4 | trailing 13 months |
| Provincial annual inflation | annual percentage change | Table 3 | trailing 13 months |
| Overall, Food and Non-Food **monthly** inflation | monthly percentage change | Table 5 | trailing 13 months |
| Annual Food and Non-Food inflation | annual percentage change | **Food:** also the "Food and Non-alcoholic Beverages" division row of Table 1; **both:** narrative text (current and previous month) and Figure 2 chart labels. **No aggregate Non-Food annual table was found in this layout** | n/a |
| District prices | absolute ZMW prices | Table 6 | current month |
| Weights | source weights, total 1,000 (section 11) | table header rows | n/a |
| Basket/coverage | text: 440 items, 116 districts, over 23,000 quotations collected 1st to 10th of the month | narrative | n/a |

The aggregate "Food" of Annex Table 1.1 and Table 5 and the "Food and Non-alcoholic Beverages" division of Table 1 share weight 534.85 and gave the same October 2026 annual value (5.7), but **they are different source structures and must not be treated as equivalent**; no source states that they are the same series.

October 2026 headline: annual 6.3% (6.1% in September); annual food 5.7%, annual non-food 7.4%; monthly 0.7% (food 0.3%, non-food 1.2%); All Items index 564.62 (food 634.10, non-food 484.73).

### Diagnostic consistency checks (not a validation invariant)

ZamStats' own note (Vol 117 and Vol 164): **"Inflation rates are computed using unrounded consumer price indices."** Printed index levels are therefore rounded displays and the **published rates are the authoritative source observations.**

The percentage change is `((index_t / index_reference) - 1) x 100`, where `index_reference` is the previous month (monthly) or the same month a year earlier (annual). As diagnostic checks only, this was recomputed from the printed 2-decimal indices and rounded half-up to one decimal:

- annual All Items rate: matched the published 1-decimal rate in all 46 months of Table 1.3 that have a printed index 12 months earlier;
- monthly All Items rate: matched in all 13 months of Table 5;
- October 2026 annual Food (634.10 / 600.12) and Non-Food (484.73 / 451.32) reproduced 5.7 and 7.4.

These are **sampled diagnostic consistency checks**. They are not a parser invariant: hidden unrounded precision can legitimately make a recomputation differ by 0.1 in other months or layouts. Parser rule for the future importer:

1. Preserve the **source-published rate** (text and Decimal).
2. Optionally compute the diagnostic from printed indices.
3. Flag an unexpectedly large discrepancy for review (never fail a month merely because of a one-unit rounding difference).
4. **Never substitute or repair a published rate with a recomputed value.**

## 5. Reference/base-period history and regimes

Documented in the bulletins:

| Regime | Used for headline | Reference period | Weights/basket | Classification | Method |
|---|---|---|---|---|---|
| **Old CPI** | up to and including **December 2011** (Vol 105: annual 7.2% for December 2011) | **1994 = 100** | 1993/94 Household Budget Survey; 357 products, 45 districts, 2,115 outlets | 8 divisions | arithmetic mean; base-price reference period |
| **Revised CPI** | **from January 2012** (Vol 106: "the first CPI based on the new methodology"; annual 6.4%) | **2009 = 100** | 2002/03 Living Conditions Monitoring Survey (LCMS III); 438 products at launch (440 in 2016 to 2026), 53 districts, 2,515 outlets | 12 divisions (COICOP) | geometric mean; previous-month price reference; provincial CPIs |

Evidence and caveats:
- **Parallel series.** Vol 105 says the new series "has been produced parallel to the old since January 2010", and Vol 117 (Dec 2012) republishes new-regime index levels for **January 2010 to December 2011** (Food/Non-Food table). The first *headline* month of the new regime is January 2012.
- **Contradiction inside the source.** Vol 93 (Dec 2010) planned a launch "in early 2011" with a three-month parallel run from January 2011; Vol 105 and Vol 106 show January 2010 (parallel) and January 2012 (launch). The later statements match the actual first headline. The earlier one is recorded as an unfulfilled plan.
- **Same month, two official values.** January 2011 annual inflation: **9.0%** (Vol 94, old regime) versus **6.3%** (Vol 117 and Vol 164, `2009 = 100` back-extension). December 2011: **7.2%** (Vol 105) versus **6.0%** (Vol 117). These are different measurements, not a revision.
- **Retrospective tables reaching before 2010 exist and were previously under-reported.** Vol 117 (Dec 2012) contains a table titled **"CONSUMER PRICE INDICES (2009=100)"** with columns "Annual CPI" and "Average Annual Inflation Rate": annual rows from **1965** and **monthly rows from January 1986** (for example January 2009 = 95.96, December 2008 = 94.71, annual 16.6, which equals the old regime's originally published December 2008 headline). Vol 94 contains a **yearly** year-on-year inflation table 1990 to 2009 (old regime; December-based values such as 2008 = 16.6, 2009 = 9.9). A claim that Vol 94 carries monthly old-regime annual-rate rows back to January 2006 could not be reproduced: the 2006-based monthly rows found there belong to an interest-rate table.
- **A base/reference-period label is not proof of shared lineage.** The retrospective "2009 = 100" rows before 2010 may be old-regime values re-expressed on a new base, which would not make them the same measurement as the current regime; the retrospective index for 1986 to 2009 reaches values such as 0.02 in 1986. No ZamStats text read explains how those rows were built. **These earlier retrospective tables must be classified before any implementation ingests them.**
- **Conservative Phase 1 boundaries:** `2010-01` for the current-regime index and `2011-01` for current-regime annual inflation remain the boundaries, **pending classification of the earlier retrospective tables**. These dates do not prove that earlier published observations do not exist.
- **No later rebasing found in the sampled 2014 to 2026 bulletins.** Vol 261, 273 and 283 contain none of "rebas", "new base", "Household Budget" or "LCMS". A future base change is a monitored risk (section 19).
- **No splice** between `1994 = 100` and `2009 = 100` was published in any bulletin read; none is assumed.

## 6. Historical coverage matrix (per metric, source-published values only)

Rule: no monthly-rate continuity is inferred from annual or index tables, and no gap is filled by calculation. "Verified" means seen in a bulletin read; "continuity" means every month in the range was seen across the sampled bulletins, **not** an archive-wide proof.

| Series (national, `2009 = 100`) | Earliest verified source-published month | Latest verified | Continuity | Source / layout family | Notes and gaps |
|---|---|---|---|---|---|
| **All Items index** | **2010-01** (105.01; retrospective rows exist from 1986-01, classification pending) | 2026-10 (564.62) | sampled bulletins' windows overlap from 2010 to 2026; **not archive-proven** | Food/Non-Food national table (Vol 117, 2012 family) to Annex 1.1 (Vol 283); annual table with index (Annex 1.3 family) | 2013 to 2014 months rest on one bulletin (Vol 164) |
| **All Items monthly inflation** | **2011-01** (0.7; "Jan '11 - Dec '10", Vol 117) | 2026-10 (0.7) | **continuous monthly-rate coverage unverified pending the implementation publication manifest** | month-on-month table (Vol 117 family) to Table 5 (Vol 283, 13-month window) | the current table covers only 13 months; history needs overlapping bulletins; the old monthly table location is unverified |
| **All Items annual inflation** | **2011-01** (6.3; Vol 117, 164); retrospective rows exist earlier (classification pending) | 2026-10 (6.3) | sampled annual-table windows overlap 2011 to 2026; **not archive-proven** | annual table (Vol 164 to Vol 283 Annex 1.3 family) | the Oct 2020 text and the Dec 2016 internal inconsistency (section 7) |
| **Food index** | **2010-01** (104.43) | 2026-10 (634.10) | as All Items index | Food/Non-Food table family (Vol 117 to Annex 1.1) | aggregate Food index; not the division index of Annex 1.2 |
| **Food monthly inflation** | **2011-01** (1.1) | 2026-10 (0.3) | **continuous monthly-rate coverage unverified pending the implementation publication manifest** | month-on-month Food/Non-Food table family to Table 5 | |
| **Food annual inflation** | table-published **2011-01** (4.2; Vol 117 "Annual Inflation Rates: Food and Non-Food Items"); the 13-month "Food and Non-alcoholic Beverages" division row of Table 1 in current bulletins | 2026-10 (5.7) | unverified | two different structures (aggregate-Food table in the 2012 family; division row in the current family) | **deferred from Phase 1** (section 14) |
| **Non-Food index** | **2010-01** (105.67) | 2026-10 (484.73) | as All Items index | as Food index | |
| **Non-Food monthly inflation** | **2011-01** (0.2) | 2026-10 (1.2) | **continuous monthly-rate coverage unverified pending the implementation publication manifest** | as Food monthly | |
| **Non-Food annual inflation** | table-published **2011-01** (8.6; Vol 117, Vol 164) | narrative and Figure 2 labels only in Vol 283 (7.4); **no aggregate table found in the current layout** | unverified | table (2012 and 2016 families); narrative and chart (current family) | **deferred from Phase 1** |

Not claimed: any claim of continuous coverage across 2003 to 2026 for any row above; monthly-rate coverage before 2011; the contents of the 251 bulletins not read. The three "unverified pending the implementation publication manifest" rows do not block Phase 1, because the importer works from a vetted manifest that either proves each month or reports the gap.

## 7. Overlap and revision evidence

Method: the annual table (index plus annual rate) of eight bulletins was extracted as source strings and compared pairwise on shared months.

| Pair | Shared months | Value differences |
|---|---|---|
| Oct 2026 vs Dec 2025 / Dec 2024 / Dec 2023 | 48 / 36 / 24 | **0** |
| Dec 2025 vs Dec 2024 / Dec 2023 | 46 / 34 | **0** |
| Dec 2024 vs Dec 2023 / Dec 2021 | 48 / 24 | **0** |
| Dec 2023 vs Dec 2021 | 24 | **0** |
| Dec 2021 vs Dec 2020 / Dec 2019 | 36 / 24 | **0** (Oct 2020 text differs, below) |
| Dec 2020 vs Dec 2019 | 36 | **0** |
| Dec 2019 vs Dec 2016 | 24 (2015 to 2016) | **0** |

(Counts are months where both extractions found a row. The Dec 2025 extraction started at March 2021 instead of January 2021 because two rows carry a year prefix the throwaway parser missed. This is a parser limit to design around.)

Findings that shape the design:
1. **No numeric revision of a published national index or annual rate was found** in 8 bulletins over about 10 years. This does not prove revisions are impossible.
2. **Formatting-only difference:** October 2020 is `274.20` in the Dec 2020 bulletin and `274.2` in the Dec 2021, 2023 and 2024 bulletins (annual 16.0 in all). Numerically equal, textually different.
3. **Internal inconsistency within one bulletin (Vol 164, Dec 2016):** the Food/Non-Food table shows Total `127.32` (Feb 2013), `136.92` (Feb 2014) and `143.52` (Aug 2014), while the annual table of the **same bulletin** shows `127.30`, `136.96` and `143.46` for those months. Two tables in one PDF disagree by 0.02 to 0.06 index points. This cannot be represented by a uniqueness rule that keys on the publication alone (section 15).
4. **Duplicated row in a source table:** Vol 117's Food/Non-Food table prints `December 110.86 107.65 114.56` twice in succession, shifting a naive row counter by one month.
5. **Layout churn:** table titles, window lengths and year labels (centred in a merged cell or prefixed to a row) vary by year.

## 8. Field semantics

- **CPI index:** unit-less level, 2009 average = 100.
- **Annual inflation:** percentage change of the index over 12 months. **Monthly inflation:** percentage change over the previous month. Both are computed by ZamStats from **unrounded** indices (section 4).
- **Contribution (percentage points):** a separate ZamStats-derived measure; out of Phase 1.
- **Division:** one of 12 COICOP divisions with its own weight. Aggregate Food (Annex 1.1, Table 5) and the Food division (Annex 1.2, Table 1) are separate source structures (section 4). Non-Food is the remaining weight (465.15).
- **Reference month** is the bulletin month: Vol 283 "The Monthly October 2026" reports October 2026 prices, collected 1st to 10th of that month. The importer must still confirm this against each table's last row.
- Footnotes: "Figures may not add up ... due to rounding off" and, for trade series, "(*) Provisional, (®) Revised". No CPI figure carried a provisional or revised marker in the bulletins read.

## 9. Precision and negative-value rules (parsing contract)

**Observed source-layout conventions** (not universal invariants): index levels 2 decimals (`564.62`); rates 1 decimal (`6.3`); weights 2 decimals (`534.85`), with some historical tables at 1 decimal (`534.9`, `1000.0`). A future parser may enforce exact precision only for a layout family whose manifest proves it. **Preserve the exact source string regardless.** BoZ's two-decimal rates (`6.30`) are BoZ's formatting, not ZamStats' canonical precision.

Negative notation is mixed within one bulletin: Table 1 (annual by main group) prints **parentheses** (`(0.6)`, `(1.6)`, `(0.3)`); Table 5 (monthly) prints a **minus sign** (`-0.3`, `-0.2`); contribution tables print `(0.0)` for a small negative that rounds to zero; 2012 bulletins print `-0.1`. `(0.6)` is read as `-0.6`: the Transport index fell from 638.53 (Oct 2024) to 635.01 (Oct 2025), -0.55%, which rounds to the printed `(0.6)`. Thousands separators appear as a comma (`1,000.00`) or a space (`1 000`) in weights.

Parser contract (design only):
1. For **every** source number retain: the **exact source text**; the parsed `Decimal` value; and **source sign/notation metadata** (for example `source_negative` and `source_notation` = `parentheses` / `minus` / `none`). Never use `float`.
2. Parenthesised zero `(0.0)` parses to a mathematical zero, but the record must keep `source_negative = true` and the text `(0.0)`: the source presented a rounded negative value, and **SQL `NUMERIC` does not preserve signed zero**, so the evidence must live in explicit fields, not in the numeric value.
3. Accept `-x.y`, `(x.y)` and plain `x.y`. Reject `+x.y`, exponents, bare `.5`, the Unicode minus and any unknown notation (fail closed).
4. A blank, dash, `n/a`, `..` or missing cell is "not published" and never becomes zero. Zero is only an explicit `0.0`/`0.00` (or `(0.0)`/`-0.0`).
5. Thousands separators are stripped only in weight and index columns after validating the grouping.
6. Reject a table with an unexpected row or column count or a non-contiguous month sequence.

## 10. All Items vs components

| Component | Index level | Monthly rate | Annual rate (table-published) |
|---|---|---|---|
| All Items | yes (Annex 1.1, 1.2, 1.3) | yes (Table 5) | yes (Annex 1.3, Table 1) |
| aggregate Food | yes (Annex 1.1) | yes (Table 5) | 2012 and 2016 families: table; current family: narrative and chart; **plus** the Food division row of Table 1 (a separate structure) |
| aggregate Non-Food | yes (Annex 1.1) | yes (Table 5) | 2012 and 2016 families: table; current family: narrative and chart only |
| 12 divisions | yes (Annex 1.2) | not located in Vol 283 | yes (Table 1, 13 months) |

Policy: prefer source-published statistics; do not store derived rates as observations. **Future derivation (not now):** if annual Food or Non-Food rates were later calculated from stored index levels they would be classified as **`derived`**, never source-published, and would need a methodology id, the exact current and prior-year source observations (values and raw strings), their publication identities, the formula `((index_t / index_{t-12}) - 1) x 100`, a rounding policy and the source-precision caveat (indices are rounded displays of unrounded values). A derived value must never silently replace a source-published one.

## 11. Weights

- **Scale:** the national total is **1,000** (printed `1 000`, `1,000.00`, `1000.0`). Division weights in that scale: Food and Non-alcoholic Beverages 534.85, Alcoholic Beverages and Tobacco 15.21, Clothing and Footwear 80.78, Housing 114.11, Furnishing 82.36, Health 8.15, Transport 58.08, Communication 12.94, Recreation and Culture 13.84, Education 26.62, Restaurants and Hotels 3.37, Miscellaneous 49.69 (sum 1000.00). Province weights also sum to 1000.00.
- **Effective period:** unchanged from the January 2012 launch in every sampled bulletin (Vol 117, 164, 201, 283: Food 534.85); the CPI is fixed-weight (Vol 164 glossary). The old regime's weights were not read.
- **Design:** weights belong with divisions (Phase 2). Store the source text, an explicit `weight_scale`, and the regime; never normalise silently.

## 12. BoZ cross-check

Feed: `https://www.boz.zm/api/v1/views/inflation_rates`, 200 objects, fields `annual_overall_inflation_rate`, `annual_food_inflation_rate`, `annual_non_food_inflation_rate`, `monthly_overall_inflation_rate`, `monthly_food_inflation_rate`, `monthly_non_food_inflation_rate` (strings, 2 decimals, `-` for negatives), `inflation_date` (HTML-wrapped `<time>`), `inflation_description` (free text), `next_update` (free text).

| Check | Months | Result |
|---|---|---|
| Annual overall, 2022-01 to 2026-10 | 57 | 56 equal; 1 apparent mismatch caused by a **BoZ labelling error** (below) |
| Annual overall, 2011-01 to 2016-12 | 72 | 71 equal; **April 2014: BoZ `7.70`, ZamStats `7.8`** (Vol 133, 141, 164 print 7.8) |
| Monthly and food/non-food | spot checks (Oct and Sep 2026; Jan 2011; Jan and Feb 2012) | matched; not compared exhaustively |

Issues: the reference month is not a field (inferred from free text or a placeholder date); the row dated 2026-06-25 reads "May inflation declines" but carries June figures and **May 2026 has no row**; January 2011 is 6.30 (new regime) although ZamStats' original headline was 9.0 (old regime), so BoZ carries restated values without a regime flag; there are no index levels, weights, divisions or provincial data; `next_update` is free text. Role: **official cross-check and a release-detection signal** (the October row appeared on 2026-10-29, the day of the bulletin). It is not canonical.

## 13. Rights and reuse

Evidence (ZamStats FAQ, `https://www.zamstats.gov.zm/faqs/`, read 2026-11-22):
- Q8: "Can I use the data published on this website for reporting or research? Yes, ZamStats encourages the use of its data for reporting or research purposes. However, it is strictly prohibited to adjust any figures."
- Q12: "Can I use ZamStats data for commercial purposes or in the private sector? While most of our services are provided free of charge, the use of ZamStats data for commercial purposes may be subject to specific terms and conditions. For inquiries regarding commercial use, please contact our Legal Department."
- Site footer: "Copyright © 2024 Zambia Statistics Agency. All Rights Reserved". No dedicated terms page was found (the guessed URLs return 404). The Statistics Act No. 13 of 2018 was not read. Bulletins print "Source: ZamStats, Prices Statistics, 2026".

Assessment: reporting/research use is encouraged and **altering figures is prohibited**; commercial use, bulk republication and public API redistribution are not addressed. **`rights_status = review_required`**, local/internal use only. Implications: show figures exactly as published, never recompute or adjust them; label any derived view as a ZM Terminal calculation. A written clarification from ZamStats' Legal Department is required before public deployment.

## 14. Phase 1 scope (narrowed and unambiguous)

Phase 1 SHALL include **only these seven source-published national series**, all on the `2009 = 100` regime (the current regime and its classified back-extension):

| # | Measure | Series |
|---|---|---|
| 1 | CPI index | All Items |
| 2 | CPI index | Food |
| 3 | CPI index | Non-Food |
| 4 | Monthly inflation | All Items |
| 5 | Monthly inflation | Food |
| 6 | Monthly inflation | Non-Food |
| 7 | Annual inflation | All Items |

Boundaries: current-regime index from **2010-01**, annual inflation from **2011-01** (and monthly inflation from 2011-01), as **conservative boundaries pending classification of the earlier retrospective tables** (section 5).

**Explicitly deferred:** annual Food inflation and annual Non-Food inflation (**both**, even though annual Food appears source-published in some layouts, so the first slice stays symmetric, stable and deterministic); calculated annual component rates; the 12 divisions and weights; provincial series (all nine provinces are present in the bulletins read, but "Northern/Muchinga" is merged and a province dimension adds complexity); the old `1994 = 100` regime; contributions; district prices; real-rate analytics, inflation-versus-policy-rate, `REAL RATE ZM`, `MISERY ZM` and any other derived analytics; and cross-regime charts.

## 15. Latest-release semantics

- Bulletin M reports CPI for M and is released in the last week of M (BoZ release dates 2026-02-27, 03-26, 04-30, 06-25, 07-30, 08-27, 09-24, 10-29; the Vol 283 PDF was created 2026-10-29). Prices are collected 1st to 10th.
- `latest` means **the newest reference month ZamStats has published**, taken from the bulletin, never the scrape date or the current calendar month. On 2026-11-22 it is **October 2026**; November appears with Vol 284. No release calendar was found; "latest" is defined by what is published. Never infer an unpublished month or forward-fill.
- The reference month must be confirmed against the bulletin title and the last row of each table; a disagreement fails closed.

## 16. Provenance and storage model (conceptual; no SQL, no migration)

**Source evidence and the canonical logical observation are not the same thing.** Four conceptual families:

1. **`cpi_publications`**: one row per source file: reference month, archive label, PDF cover volume/title, URL, content SHA-256, layout family, `retrieved_at`, `rights_status`, PDF `CreationDate` and HTTP `Last-Modified` (informational only).
2. **`cpi_source_observations`**: immutable **source-cell evidence**, one row per cell read: publication, source table id (for example `Annex 1.1`), PDF page, source row index and label, source column/category, metric, reference month, exact source text, parsed `Decimal`, source-sign metadata (section 9), regime/reference period. Identity may include `publication_id`, `source_table_id`, `source_row`, `reference_month` and the series identity, so **two conflicting cells for the same logical observation inside one bulletin (Vol 164, Feb 2013: `127.32` and `127.30`) are both preserved.**
3. **`cpi_index_observations`**: the resolved canonical logical index value per `(reference_month, series identity, regime)`.
4. **`cpi_inflation_observations`**: the resolved canonical logical rate per `(reference_month, series identity, regime, rate_type = monthly or annual)`.

An index level, a monthly change and an annual change are different quantities with different units and validity, so index and inflation stay in separate canonical families with structural `CHECK`s (a single `measure` column that invites charting them together is rejected). A canonical row records which source observations support it and whether resolution is `agreed`, `unresolved` or `reviewed`.

### Conflict policy

- **Identical publication/content** (same file hash): no-op.
- **Same numeric value, different source formatting** (`274.20` vs `274.2`): not source-text identical. Preserve the exact source text of each publication as evidence; the canonical value is the same `Decimal`; this is not a conflict.
- **Different numeric values for the same logical observation:**
  - **within one publication:** preserve all conflicting source cells, leave canonical resolution **unresolved** and **fail the import/canonicalization step closed** unless a reviewed source rule resolves it. Never overwrite one with the other.
  - **across publication vintages:** preserve both vintages; serve neither as current until an explicit canonical/revision policy decides.
- **Different regime or reference period:** a different series, never a revision.

## 17. Importer, manifest and parser requirements

**Prerequisite before any real import: a vetted publication manifest** for the Phase 1 coverage period. Each entry identifies: reference month; publication URL; archive label; PDF cover/title/volume; content SHA-256; layout family; relevant source pages and tables; expected metric coverage; and regime/reference period. The manifest must explicitly catch **duplicate archive volume labels** (for example "Vol 121" on April and May 2013), **missing months, duplicate months, conflicting PDFs and unexpected layout changes**. Discovery keeps distinct URLs, files, hashes and archive labels, records numbering inconsistencies as provenance, and **never discards a publication because its displayed volume repeats**. The importer does not iterate the archive generically at import time. Real import is conditional on this manifest, and it is what settles the "continuous monthly-rate coverage unverified" rows of section 6.

**Parser:** a strict text-layer parser (`pdftotext -layout`, already used by earlier phases) **only for known layout families** declared in the manifest. It **fails closed** on: missing table anchors; duplicated reference months; duplicate categories; unexpected table boundaries; conflicting source cells; unknown negative notation; unexpected precision or layout changes; wrong regime or base period; and ambiguous source month or publication identity. Years are assigned by counting back from the bulletin's reference month, not from a centred year label. There is **no fuzzy table guessing and no OCR** unless separately researched and approved (the sampled files all had a text layer; verify per manifest entry). Tests use small synthetic fixtures and no live network, covering at least `(x.y)`, `-x.y`, `(0.0)`, `274.2` vs `274.20`, a duplicated row, a year-prefixed first row and an incomplete table.

## 18. API and frontend (design only)

**API.** `GET /api/v1/cpi/latest` and `GET /api/v1/cpi/history`, named for the index-first source model, with three separate sections so a level and a rate are never interchangeable:

```json
{
  "reference_month": "2026-10", "regime": "2009=100",
  "index": {"all_items": {"value": "564.62", "source_value_text": "564.62"}, "food": {}, "non_food": {}},
  "annual_inflation_pct": {"all_items": {"value": "6.3", "source_value_text": "6.3"}},
  "monthly_inflation_pct": {"all_items": {}, "food": {}, "non_food": {}},
  "source": "ZamStats", "source_url": "...", "retrieved_at": "...", "rights_status": "review_required"
}
```

Values are JSON decimal strings; source sign metadata is returned where relevant. Annual Food/Non-Food are absent in Phase 1. `INFLATION ZM` is a command alias, not a second route.

**Frontend.** A card such as `INFLATION ZM / 6.3% / ANNUAL · OCT 2026 / ZAMSTATS`, shown only because the All Items annual rate is source-published. Detail view: annual headline; monthly inflation; food and non-food monthly rates; index levels; monthly history chart; provenance; a base-period notice (`2009 = 100`).

**Regime and chart wording.** The original headline before January 2012 used `1994 = 100`; ZamStats retrospectively published `2009 = 100` observations for 2010 to 2011; Phase 1 contains only classified `2009 = 100` observations, so **a Phase 1 chart does not need a regime break at January 2012.** A chart break is required only in a future view that deliberately combines old-regime `1994 = 100` and current-regime `2009 = 100` data; **those regimes must never be silently joined into one continuous index series.**

## 19. Unresolved risks

1. **Rights:** ZamStats commercial/redistribution terms are unclear; `review_required` until written clarification.
2. **Retrospective lineage:** the "CONSUMER PRICE INDICES (2009=100)" tables from 1986 and the January 2010 to December 2011 back-extension need classification before ingestion beyond the Phase 1 boundaries.
3. **Layout churn:** only 32 of 283 bulletins were read; table titles, windows, year labels and duplicated rows vary. A manifest dry run is needed before backfill.
4. **Within-bulletin inconsistency** (Dec 2016, three months) shows tables can disagree; the evidence model preserves both, but the three affected months need a reviewed decision before canonicalization.
5. **Archive numbering inconsistencies** (two "Vol 121" entries) mean volume labels cannot identify a publication.
6. **Future rebasing** after 14 years of `2009 = 100` is plausible; the regime is part of the series identity and a base-change announcement must stop the importer.
7. **Continuous monthly-rate coverage** is unverified (section 6).
8. **Hidden source precision:** published rates come from unrounded indices; printed-index recomputation is only diagnostic.
9. **BoZ feed** can mislabel, omit or restate months; cross-check only.
10. **No machine-readable source:** the project depends on PDF layout stability; the Cloudflare-protected portals were not worked around.
11. **Annual Food/Non-Food** have different table structures across layout families; deferred.

## 20. Implementation decision

**APPROVED FOR LOCAL/INTERNAL PHASE 1 IMPLEMENTATION**, with **`rights_status = review_required`**.

Scope:

```text
2009=100 national series only

INDEX:
- All Items
- Food
- Non-Food

MONTHLY INFLATION:
- All Items
- Food
- Non-Food

ANNUAL INFLATION:
- All Items
```

Approval is **conditional on a vetted publication manifest and layout-family contract before controlled real import** (section 17). Deferred: annual Food/Non-Food inflation; the old `1994 = 100` regime; provincial series and divisions; derived analytics (derived annual component rates, `REAL RATE ZM`, inflation versus the BoZ policy rate or T-bill yields, `MISERY ZM`). Public deployment additionally needs ZamStats' written terms.
