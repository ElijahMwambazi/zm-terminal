# Zambia CPI and inflation: source discovery (research only)

Status: **research only**. Nothing here is implemented. No parser, migration, table, route, frontend component or existing dataset was changed, and no data was imported. Research date: 2026-11-22. Base: `main` at `fd48c5f` (PR #41, Bank FX frontend).

## 1. Executive recommendation

- **Canonical publisher: Zambia Statistics Agency (ZamStats)**, via its **Monthly Bulletin** PDFs. The bulletins are anonymous, text-layer PDFs (no OCR needed), listed on one archive page, with an unbroken archive from Vol 1 (April 2003) to Vol 283 (October 2026).
- **There is no official machine-readable CPI endpoint that can be used.** The WordPress REST API is public but carries no CPI data. ZamStats' data portals (`*.opendataforafrica.org`) sit behind a Cloudflare JavaScript challenge, which was not bypassed; `nada.zamstats.gov.zm` and `zamb.opendataforafrica.org` did not answer. The data source is therefore PDF text extraction, as for ERB and BoZ PDFs elsewhere in this project.
- **Index reference period: `2009 = 100`, unchanged from the January 2012 bulletin to the October 2026 bulletin.** Weights are identical across that span (Food and Non-alcoholic Beverages 534.85 of 1,000.00 in 2012, 2016, 2019 and 2026). Before January 2012 the headline CPI used a **different regime** (`1994 = 100`, 8 divisions, 1993/94 weights). The two regimes give different values for the same month and must never be spliced (section 6).
- **Bank of Zambia (BoZ) is a secondary cross-check only.** BoZ exposes a clean JSON feed (`/api/v1/views/inflation_rates`, 200 rows, February 2010 to October 2026) with six rate fields, but it republishes ZamStats imperfectly: one mislabelled month, one missing month and one differing value were found (section 12). It carries no index levels, no weights and no divisions.
- **Rights: `review_required`**, local/internal use only (section 13).
- **Smallest useful Phase 1 (recommended): national monthly CPI**: All Items, Food and Non-Food **index levels** (Annex Table 1.1), **annual overall inflation** (Annex Table 1.3) and **monthly overall/food/non-food inflation** (the monthly table in each bulletin), regime `2009 = 100`, January 2012 onward as the contiguous base and January 2010/2011 as the back-extension (section 14). Divisions, weights, provincial data and the pre-2012 regime are Phase 2 or research-only.
- **Implementation is approved for local/internal use** (section 20), with the open points in section 19 stated honestly.

## 1a. Verification status

**Verified by reading downloaded PDF text layers (`pdftotext -layout`):**
- 32 of 283 bulletins were downloaded and read where relevant: Vol 1 (Apr 2003), Vol 9, 21, 33, 45, 57 (Dec 2003 to Dec 2007), Vol 69 (Dec 2008), 81 (Dec 2009), 93 (Dec 2010), 94 to 97 (Jan to Apr 2011), 105 (Dec 2011), 106 (Jan 2012), 117 (Dec 2012), 129 (Jan 2014), 133 (May 2014), 141 (Jan 2015), 153 (Jan 2016), 164 (Dec 2016), 165 (Jan 2017), 177 (Jan 2018), 201 (Dec 2019), 213 (Dec 2020), 225 (Dec 2021), 249 (Dec 2023), 261 (Dec 2024), 271 and 273 (Oct and Dec 2025), 282 and 283 (Sep and Oct 2026).
- Volume 283 (October 2026) was read in full for the CPI sections, including every annex CPI table.
- BoZ's inflation feed was downloaded in full (200 rows) and compared programmatically with ZamStats tables.

**Not verified:**
- Bulletins not downloaded (251): their CPI sections were not read. The overlap test used 8 annual-table bulletins (Dec 2016 to Oct 2026); the Dec 2012 bulletin was used for the 2010 to 2012 tables and the Jan 2011/Dec 2011 regime comparison.
- Numeric annual and monthly rates before 2008 (the text states them, but they were not extracted); the old-regime index levels (`1994 = 100`) were not found in any bulletin read.
- The Statistics Act No. 13 of 2018 and any formal terms of use (no terms page exists at the guessed URLs; only the FAQ was found).
- Any ZamStats metadata or methodology document beyond the bulletins themselves.

## 2. Method and limits

- ZamStats pages and PDFs were fetched with plain anonymous `curl` and a research User-Agent, sequentially with a one-second pause. `robots.txt` disallows only `/wp-admin/`.
- The `*.opendataforafrica.org` portals returned a Cloudflare "Just a moment… Enable JavaScript and cookies" page (HTTP 403). Per the research brief this was **not** worked around.
- BoZ's website is a single-page application; its public bundle names the same Drupal JSON:API base already used by this project's BoZ importers (`https://www.boz.zm`). The inflation view was read anonymously.
- Throwaway parsers used to compare tables live only in the session scratchpad. No parser, PDF or table content is committed; figures below are quoted only to justify source decisions.
- Date and place of every claim is a bulletin volume and table name, so a later reader can re-check it.

## 3. Official sources and exact URLs

| # | Publisher | Resource | URL | Format | Verdict |
|---|---|---|---|---|---|
| 1 | ZamStats | Monthly Bulletin archive (index page) | `https://www.zamstats.gov.zm/monthly-bulletin/` | HTML, 283 PDF links Vol 1 (Apr 2003) to Vol 283 (Oct 2026) | **canonical discovery index** |
| 2 | ZamStats | Monthly Bulletin PDF, latest | `https://www.zamstats.gov.zm/wp-content/uploads/2026/10/Vol-283-of-2026-The-Monthly-October.pdf` (2.4 MB, 59 pages, text layer, PDF created 29 Oct 2026, `Last-Modified` 29 Oct 2026 12:57 GMT) | PDF | **canonical data** |
| 3 | ZamStats | WordPress REST root | `https://www.zamstats.gov.zm/wp-json/` | JSON | public, but only WordPress/SEO/Elementor routes; no CPI dataset |
| 4 | ZamStats | Data portals | `nso-zambia.opendataforafrica.org`, `zambiainfohighway.opendataforafrica.org` (both Cloudflare challenge), `zamb.opendataforafrica.org`, `nada.zamstats.gov.zm` (no answer) | n/a | **not usable**; access control not bypassed |
| 5 | ZamStats | FAQ | `https://www.zamstats.gov.zm/faqs/` | HTML | rights evidence (section 13) |
| 6 | BoZ | Inflation view | `https://www.boz.zm/api/v1/views/inflation_rates` | JSON, 200 rows, 99 KB | **cross-check only** |

Bulletin URL pattern: `.../wp-content/uploads/{YYYY}/{MM}/Vol-{N}-of-{YYYY}-The-Monthly-{Month}.pdf`. File names are irregular (`...-Flnal.pdf`, `...-241225.pdf`, `Vol_276-of-...`, older ones under `/2023/12/` in the scraped `http://` form), so the archive page must be parsed, not guessed. Several volumes were re-uploaded (the same month under two paths), so discovery has to deduplicate by volume number.

## 4. Current publication contract (Volume 283, October 2026)

Each bulletin has a **narrative CPI section** (pages 3 to 9) and a **CPI annex** (Annex 1, Tables 1.1 to 1.4, near the end of the PDF). The measures are distinct:

| Item | What it is | Where | Window per bulletin |
|---|---|---|---|
| All Items / Food / Non-Food CPI | **Index level**, `2009 = 100`, 2 decimals (e.g. `564.62`) | Annex Table 1.1 | January 2023 to the reference month (about 46 months, 4 calendar years) |
| All Items CPI by 12 divisions | **Index level** | Annex Table 1.2 | same window |
| CPI index and annual inflation, all items | index level plus **annual percentage change**, 1 decimal | Annex Table 1.3 | January 2022 to the reference month (about 58 months) |
| CPI by province | **Index level**, 9 province columns plus national | Annex Table 1.4 | January 2023 to the reference month |
| Annual inflation by CPI main group (12) | **Annual percentage change**, 1 decimal | Table 1 (narrative) | trailing 13 months |
| Contribution of main groups | **Percentage-point contribution** (derived by ZamStats), 1 decimal | Table 2 | trailing 13 months |
| Provincial annual inflation | **Annual percentage change**, 1 decimal | Table 3 | trailing 13 months |
| Provincial contribution | percentage-point contribution | Table 4 | trailing 13 months |
| Overall monthly inflation, Food, Non-Food | **Monthly percentage change**, 1 decimal | Table 5 | trailing 13 months |
| Annual Food and Non-Food inflation | **Annual percentage change**, 1 decimal | narrative text (current and prior month) and **Figure 2 chart data labels only** | no table in the current layout |
| District price quotations | **Absolute prices (ZMW)** for selected products | Table 6 | current month only |
| Basket size and coverage | descriptive text: 440 items, 116 districts, over 23,000 quotations collected 1st to 10th of each month | narrative | descriptive |
| Weights | **Source weights**, sum 1,000 (section 11) | header row of Tables 1, 2, 5, 1.1, 1.2, 1.4 | n/a |

The headline of October 2026: annual 6.3% (6.1% in September), annual food 5.7%, annual non-food 7.4%, monthly 0.7% (food 0.3%, non-food 1.2%), All Items index 564.62 (food 634.10, non-food 484.73).

Consistency check (verified arithmetically): the **annual rate published in Table 1.3 equals the rounded ratio of the published index levels**, `round_half_up((CPI_t / CPI_{t-12} - 1) x 100, 1)`, for all 46 months with an index 12 months earlier (zero mismatches). The monthly overall rates of Table 5 reproduce the same way for all 13 months. Annual food (5.7) and non-food (7.4) for October 2026 also reproduce from Table 1.1. This establishes that every published rate is derivable from published indices; it does not make derived values official (section 10).

## 5. Reference/base-period history and regimes

Documented in the bulletins themselves:

| Regime | Used for headline | Reference period | Weights/basket | Classification | Method |
|---|---|---|---|---|---|
| **Old CPI** | up to and including **December 2011** (Vol 105 headline: annual 7.2% in December 2011) | **1994 = 100** | 1993/94 Household Budget Survey; 357 products, 45 districts, 2,115 outlets | 8 divisions | arithmetic mean; base-price reference period; metropolitan low/high and non-metropolitan compilation |
| **Revised ("new") CPI** | **from January 2012** (Vol 106: "the first CPI based on the new methodology"; annual 6.4%) | **2009 = 100** | 2002/03 Living Conditions Monitoring Survey (LCMS III); 438 products at launch (440 in 2016 to 2026), 53 districts, 2,515 outlets | 12 divisions (COICOP) | geometric mean; previous-month price reference; provincial CPIs |

Evidence and caveats:
- **Parallel series.** Vol 105 (December 2011) states the new series "has been produced parallel to the old since January 2010", and Vol 117 (December 2012) republishes new-regime index levels for **January 2010 to December 2011**. So the new regime has back-extended index levels but its first *headline* month is January 2012.
- **A contradiction inside the source.** Vol 93 (December 2010) says the new CPI would run in parallel "from January 2011 for 3 months" and launch "in early 2011"; Vol 105 says January 2010 and a January 2012 launch. The later statements match the actual first headline (Vol 106). The earlier one is recorded as an unfulfilled plan, not as a regime date.
- **Same month, two official values.** January 2011 annual inflation: **9.0%** in Vol 94 (old CPI) versus **6.3%** in Vol 117 and Vol 164 (new CPI back-extension). December 2011: **7.2%** (Vol 105, old) versus **6.0%** (Vol 117, new). The two are different measurements, not a revision of one series.
- **No splice** was published. No bulletin read links `1994 = 100` to `2009 = 100` with a conversion factor. Old-regime index levels were **not found** in any bulletin read (only annual rates by main group, and a yearly year-on-year table from 1990 in Vol 96).
- **No later rebasing found.** Every bulletin sampled from Vol 129 (January 2014) through Vol 283 carries `2009 = 100` and the same weights. Searching Vol 261, 273 and 283 for "rebas", "new base", "Household Budget" and "LCMS" found nothing. A rebasing after 2026 remains possible and is a monitored risk (section 19).
- **Historical revision of the old series:** none documented.

## 6. Historical depth (separately, as requested)

| Series | Earliest verified monthly observation | Notes |
|---|---|---|
| **A. All Items CPI index** | **January 2010** (105.01, `2009 = 100`, Vol 117 Table "Consumer Index Numbers of Consumer Prices") | contiguous to October 2026 through overlapping bulletins; pre-2010 and old-regime (1994 base) index levels not found |
| **B. Annual inflation (new regime)** | **January 2011** (6.3%, Vol 117 and Vol 164) | needs the January 2010 index; contiguous to October 2026 |
| **B2. Annual inflation (old regime, 1994 base)** | monthly values verified table-level from **January 2008** (Vol 69: 9.3% rising to 16.6% in December 2008; the 12 columns are read as January to December from the text, the header months were not extracted); text-level statements from **December 2003** (Vol 9) and a chart from November 2002 (Vol 1) | not comparable with the new regime; narrative or table only, no index |
| **C. Monthly inflation (new regime)** | **January 2011** ("Jan '11 - Dec '10": 0.7, Vol 117) | the monthly table in each bulletin covers only the trailing 13 months, so history is assembled across bulletins |
| **C2. Monthly inflation (old regime)** | **March 2003** (0.8%) and **April 2003** (0.2%) in Vol 1 text; the old-regime monthly table was not located | text-level only |
| **D. Food / Non-Food** | index levels **January 2010**; monthly rates **January 2011**; annual rates **January 2011** (published table in Vol 117/164); in the **current layout annual food/non-food are chart labels plus narrative only** | the annual table exists in 2012 and 2016-era bulletins; confirm for 2017 to 2025 before relying on it |
| **E. Division-level CPI / inflation** | division index levels at least **January 2012** (Vol 117 Table "Consumer Price Index (2009 = 100)", 12 divisions, 2009 weights); Vol 164 shows a composite division table from January 2014; annual main-group rates in every bulletin | 12 divisions from the new regime; the old regime had 8, with different names |
| **F. Provincial** | provincial index and annual rates **October to December 2012** in Vol 117; provincial tables in Vol 129 (Jan 2014), Vol 164, Vol 283 | earliest published provincial window not located; "Northern/Muchinga" is combined (9 columns, not 10 provinces) |

Do **not** claim continuous coverage from two distant publications. What is established: the **new-regime national series is continuous from January 2010 (index) / January 2011 (annual and monthly rates) to October 2026**, because the eight annual-table bulletins sampled cover contiguous windows (2011-2016, 2015-2019, 2017-2020, 2018-2021, 2020-2023, 2020-2024, 2021-2025, 2022-2026), overlapping each other. Months between windows not sampled (for example 2013 to 2014) rest on the Dec 2016 bulletin; a full-archive run would re-verify every month.

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

(Counts are months where both extractions found a row. The Dec 2025 extraction started at March 2021 instead of January 2021 because two rows carry a year prefix that the throwaway parser missed. This is a parser limit to design around, not a source finding.)

Findings that shape the design:
1. **No numeric revision of a published national index or annual rate was found** in 8 bulletins over about 10 years. This does not prove revisions are impossible.
2. **Formatting-only change:** October 2020 is `274.20` in the Dec 2020 bulletin and `274.2` in the Dec 2021, 2023 and 2024 bulletins (annual 16.0 in all). Numerically equal, textually different. A byte-exact conflict check would false-alarm; the check must compare `Decimal` values and keep both source strings.
3. **Internal inconsistency within one bulletin (Vol 164, Dec 2016):** the Food/Non-Food table shows Total `127.32` (Feb 2013), `136.92` (Feb 2014) and `143.52` (Aug 2014), while the annual table of the same bulletin shows `127.30`, `136.96` and `143.46` for those months. Two tables in one PDF disagree by 0.02 to 0.06 index points. A later bulletin (Vol 201) does not republish those months, so which value stands was not resolved. This is the strongest argument for keeping provenance per table and failing closed on conflicts.
4. **Duplicated row in a source table:** Vol 117's Food/Non-Food table prints `December 110.86 107.65 114.56` twice in a row (once at the 2010/2011 boundary), shifting an unwary row counter by one month.
5. **Layout churn:** the annual table, year labels (centred in a merged cell or prefixed to a row), table titles ("Consumer Index Numbers of Consumer Prices", "Consumer Price Index - Food and Non-Food (National)") and the window lengths changed across years. The parser must anchor on the bulletin's reference month and on expected row counts, not on a fixed title.

Treatment recommended for ZM Terminal:
- **Identical overlap** (same Decimal): store one observation, add a publication reference.
- **Same Decimal, different text** (`274.2` vs `274.20`): not a conflict; keep the first-seen source text per publication.
- **Different Decimal for the same identity:** a **conflict**. Store both vintages, mark the identity conflicted, serve neither as "latest" silently, and require an explicit decision (as for the BoZ and ERB importers).
- **Different reference period or regime:** a different series (the series identity includes the regime), never a revision.

## 8. Field semantics

- **CPI index:** unit-less level, base period 2009 average = 100, 2 decimals in annexes (`564.62`). Some older tables print 1 decimal (`1000.0`, weights) or 2.
- **Annual inflation:** percentage change of the index over 12 months; "Annual Inflation Rate" in Table 1.3; 1 decimal.
- **Monthly inflation:** percentage change over the previous month; 1 decimal; ZamStats calls it "Overall Monthly Inflation Rate".
- **Contribution (percentage points):** a separate measure (Tables 2 and 4); "Figures may not add up to national total due to rounding". Out of Phase 1.
- **Main group / division:** one of 12 COICOP divisions with its own weight; the narrative "Food" is division 1 ("Food and Non-alcoholic Beverages"); "Non-Food" is the remaining 465.15 of weight.
- **Reference month** = the month in the bulletin title. Vol 283 "The Monthly October 2026" reports October 2026 prices (collected 1st to 10th of that month), not an earlier month. This holds for every bulletin read (for example Vol 133 "May 2014" reports annual 7.8% for May 2014).
- Table footnotes: `*` "Figures may not add up ... due to rounding off". The `(*) Provisional, (®) Revised` convention appears for trade and other series; **no CPI figure carried a provisional or revised marker** in the bulletins read.

## 9. Precision and negative-value rules (parsing contract)

Observed in the source:
- Index levels: **2 decimals** (`564.62`), sometimes with a trailing zero dropped in later republications (`274.2`).
- Annual and monthly inflation: **1 decimal** (`6.3`, `0.7`). BoZ's feed re-prints them with **2 decimals** (`6.30`) which is BoZ's formatting, not ZamStats'.
- Weights: `534.85` (2 decimals) in annexes and the narrative; `534.9` (1 decimal) in some historical tables; the total appears as `1 000`, `1,000.00`, `1000.0` or `1 000.00`. The 12 division weights sum to 1000.00 and the 9 province weights to 1000.00 (verified for Vol 283).
- **Negative values use two conventions in the same bulletin.** Table 1 (annual by main group) prints **parentheses**: `(0.6)`, `(1.6)`, `(0.3)`. Table 5 (monthly) prints a **minus sign**: `-0.3`, `-0.2`. Contribution tables print `(0.0)` for a small negative that rounds to zero. Older bulletins (2012) print `-0.1`. The text states no rule, but `(0.6)` in Table 1 is read as `-0.6`: the Transport index fell from 638.53 (Oct 2024) to 635.01 (Oct 2025), a change of -0.55%, which rounds to the printed `(0.6)`.
- Thousands separators: a comma (`1,000.00`) or a space (`1 000`) in weights; indices below 1000 have none.

Parser contract (design, not implemented):
1. Keep the **exact source text** (`source_value_text`) per observation; derive `Decimal` from it; never use `float`.
2. Accept `-x.y`, `(x.y)` (negative), plain `x.y` (positive), and treat `(0.0)` as `Decimal("0.0")` with `source_value_text="(0.0)"`. Reject `+x.y`, exponents, bare `.5`, and unicode minus.
3. A **blank, dash (`-`, `–`, `—`), `n/a`, `..` or missing cell** is "not published" and must not become zero.
4. **Zero** is only the literal `0.0` or `0.00` (or `(0.0)`/`-0.0`).
5. Thousands separators are stripped only inside the weight and index columns after validating the grouping; any other non-numeric character fails the row.
6. Reject a table whose row count, column count or month sequence is not the expected contiguous window.

## 10. All Items vs components

| Component | Index level | Annual rate | Monthly rate |
|---|---|---|---|
| All Items | yes (Table 1.1, 1.2, 1.3) | yes (Table 1.3; Table 1) | yes (Table 5) |
| Food | yes (Table 1.1) | **narrative + chart labels in the current layout**; a full table in 2012 and 2016-era bulletins (Dec 2012 "Annual Inflation Rates: Food and Non-Food Items", Dec 2016 table) | yes (Table 5) |
| Non-Food | yes (Table 1.1) | same as Food | yes (Table 5) |
| 12 divisions | yes (Table 1.2) | yes (Table 1, 13-month window) | not located in the current bulletin |

Policy: **prefer source-published statistics; do not store derived rates as observations.** The published annual Food and Non-Food rate of recent months exists only as a chart (data labels such as `14.1  13.9  12.9  10.9 ...` extracted as loose text, not tied to a series by position) or a narrative sentence for the current and previous month. That is too ambiguous to parse safely. Because every published annual rate was shown to equal the rounded index ratio (section 4), the **index levels are sufficient to compute annual Food and Non-Food on request**, clearly labelled "computed from published index levels, not a published rate" and never persisted. Whether to ship such a derived view in Phase 1 is an owner decision (section 19).

## 11. Weights

- **Units/scale:** the national total is **1,000** (printed `1 000`, `1,000.00`, `1000.0`). Division weights are in that scale (Food 534.85, Alcoholic Beverages and Tobacco 15.21, Clothing 80.78, Housing 114.11, Furnishing 82.36, Health 8.15, Transport 58.08, Communication 12.94, Recreation 13.84, Education 26.62, Restaurants and Hotels 3.37, Miscellaneous 49.69; sum 1000.00). Province weights sum to 1000.00 (Central 107.19, Copperbelt 219.68, Eastern 88.98, Luapula 50.60, Lusaka 283.89, Northern 65.72, North-Western 32.33, Southern 109.19, Western 42.42).
- **Effective period:** unchanged from the January 2012 launch. The weights come from the 2002/03 LCMS III (Vol 105, Vol 106); the same figures appear in Vol 117 (2012), Vol 164 (2016), Vol 201 (2019) and Vol 283 (2026). No weight revision between 2012 and 2026 was found. The CPI is a **fixed-weight** index (Vol 164 glossary).
- **Old regime:** weights from the 1993/94 Household Budget Survey, 8 divisions; the old weights were not read.
- **Design:** store a weight as the source text plus an explicit `weight_scale` (`1000`) and the regime; never normalise to 100 or 1 silently. Weights belong in Phase 2 with divisions (they describe a division, not a month).

## 12. BoZ cross-check

The feed `https://www.boz.zm/api/v1/views/inflation_rates` returns 200 objects with these fields (all rates are strings with 2 decimals; negative values use `-`): `annual_overall_inflation_rate`, `annual_food_inflation_rate`, `annual_non_food_inflation_rate`, `monthly_overall_inflation_rate`, `monthly_food_inflation_rate`, `monthly_non_food_inflation_rate`, `inflation_date` (HTML-wrapped `<time datetime=...>`), `inflation_description` (free text, sometimes HTML `<p>`) and `next_update` (free text, e.g. `"November "`).

Comparison with ZamStats (Annex Table 1.3 of Vol 283 for 2022 to 2026; Dec 2016 annual table for 2011 to 2016):

| Check | Months compared | Result |
|---|---|---|
| Annual overall, 2022-01 to 2026-10 | 57 | 56 equal; 1 apparent mismatch caused by a **BoZ labelling error** (below) |
| Annual overall, 2011-01 to 2016-12 | 72 | 71 equal; **April 2014: BoZ `7.70`, ZamStats `7.8`** (Vol 133, Vol 141 and Vol 164 all print 7.8) |
| Monthly and food/non-food | spot checks only | October, September and the 2012 months matched in the rows read; not compared exhaustively |

Issues with BoZ as a source:
1. **The reference month is not a field.** It must be inferred from the free-text description or from the (publication) date, and old rows use placeholder dates (the 1st of the month).
2. **Mislabelled row:** the row dated 2026-06-25 reads "May inflation declines" but carries June figures (annual 6.5, monthly 0.1, food 6.7), which equal ZamStats' June values. There is **no row for May 2026**. Description-derived months therefore fail closed.
3. **Regime mixing without a flag:** BoZ lists January 2011 as 6.30 (the new-regime back-extension) although ZamStats' original headline for that month was 9.0 (old regime). It silently carries restated values for 2010 to 2011.
4. **No index levels, weights, divisions or provincial data.**
5. 2-decimal formatting (`6.30`) differs from ZamStats' `6.3` (precision conventions differ).
6. `next_update` is free text.

Conclusion: BoZ **republishes ZamStats values** for the headline rates (about 98% agreement in the months compared) but is not a verbatim mirror. Role: **official cross-check and a discovery signal of a new release** (the JSON row appears the day the bulletin is released: 2026-10-29 for October), never canonical.

## 13. Rights and reuse

Evidence (ZamStats FAQ, `https://www.zamstats.gov.zm/faqs/`, read 2026-11-22):
- Q8: "Can I use the data published on this website for reporting or research? Yes, ZamStats encourages the use of its data for reporting or research purposes. However, it is strictly prohibited to adjust any figures."
- Q12: "Can I use ZamStats data for commercial purposes or in the private sector? While most of our services are provided free of charge, the use of ZamStats data for commercial purposes may be subject to specific terms and conditions. For inquiries regarding commercial use, please contact our Legal Department."
- Q3: most products are free of charge.
- Q11: use official publications, "beware of unofficial sources".
- Site footer: "Copyright © 2024 Zambia Statistics Agency. All Rights Reserved".
- No dedicated terms-of-use, licence or privacy page was found (`/terms-and-conditions/` and `/privacy-policy/` return 404). The Statistics Act No. 13 of 2018 (linked from the site) was not read.
- Each bulletin prints "Source: ZamStats, Prices Statistics, 2026", so attribution to ZamStats is customary.

Assessment: reporting and research use is encouraged; **altering figures is prohibited**; commercial use, bulk republication and public API redistribution are **not addressed** and the FAQ says commercial use "may be subject to terms". Rights status recommended: **`review_required`**, local/internal use only. Implications for the design: show figures exactly as published, never rounded differently, never adjusted or seasonally smoothed; derived views must be labelled as ZM Terminal calculations from ZamStats figures. A written clarification from the ZamStats Legal Department (public display, API redistribution, derived series) would be required before public deployment.

## 14. Candidate Phase 1 and later phases

Phase 1 (recommended, smallest useful, all source-published):

| Measure | Series | Source table | First verified | Window per bulletin |
|---|---|---|---|---|
| CPI index | All Items, Food, Non-Food (national) | Annex 1.1 | 2010-01 | about 46 months |
| Annual inflation | All Items (national) | Annex 1.3 | 2011-01 | about 58 months |
| Monthly inflation | All Items, Food, Non-Food (national) | Table 5 (or its predecessor) | 2011-01 | trailing 13 months |

Not in Phase 1 but recorded:
- **Annual Food/Non-Food inflation:** published only in a chart and the current/previous-month narrative; either derive on request from stored index levels (labelled, never persisted) or defer (section 19).
- **Phase 2:** 12-division index levels and annual rates with weights (Annex 1.2, Table 1); provincial index (Annex 1.4) and provincial annual inflation (Table 3).
- **Research-only:** old regime (`1994 = 100`) values, pre-2010 data, contributions (Tables 2 and 4), district prices.

Provincial recommendation: **Phase 2**. All nine provinces are present in every bulletin read, weights sum to 1000, and the national total equals the weighted total, but the "Northern/Muchinga" merge, the window sizes, and the need for a separate geography dimension add complexity that Phase 1 does not need.

## 15. Latest-release semantics

- A bulletin for month M reports CPI for M and is released in the **last week of M**: BoZ's feed shows release dates 2026-02-27, 03-26, 04-30, 06-25, 07-30, 08-27, 09-24 and 10-29 for February to October (the PDF for Vol 283 was created 2026-10-29). The data-collection window is the 1st to the 10th.
- `latest` therefore means **the newest reference month that ZamStats has published**, taken from the bulletin's own month, never the scrape date and never the current calendar month. On 2026-11-22 the latest is **October 2026**; November appears when Vol 284 is published (expected in the last week of November; BoZ's `next_update` says "November").
- Never infer an unpublished month; never forward-fill.
- The reference month must be **parsed from the bulletin and checked against the bulletin's volume/title month and the table's last row**; a disagreement fails closed.

## 16. Provenance and storage recommendations

Fields to retain per observation (final schema to be proven by implementation research, not finalised here):

- `reference_month` (first day of the month), `measure` (`index`, `annual_pct`, `monthly_pct`), `geography` (`ZM`; later province code), `category` (`all_items`, `food`, `non_food`; later division code), `regime` / `index_reference_period` (`2009=100`).
- `source_value_text` (exact string) and `value` (`NUMERIC`, never float).
- `weight_text` and `weight_scale` where relevant (Phase 2).
- `publication` reference: bulletin volume number, bulletin title, bulletin month, source URL, SHA-256 of the PDF, `source_table` (for example `Annex 1.1`) and PDF page, `retrieved_at`, `rights_status`.
- Publication timestamp: ZamStats prints no release date inside the PDF; use PDF `CreationDate` and HTTP `Last-Modified` as informational fields only, not as business keys (both are 29 Oct 2026 for Vol 283).

**Option A: one observation table** (`reference_month, geography, category, measure, regime`) vs **Option B: separate index and rate tables**. Recommendation: **B, two tables plus a publication table**:
- `cpi_publications` (one row per bulletin file),
- `cpi_index_observations` (index level per category and month),
- `cpi_inflation_observations` (rate per category, month and `rate_type` = annual or monthly).

Reason: an index level, a monthly change and an annual change are different quantities with different units, different precisions and different validity (the annual rate needs a 12-month-earlier index, the monthly one needs the previous month). A single table with a `measure` column makes it easy to plot or average them together. Separate tables, with `CHECK` constraints on the allowed ranges and the `rate_type`, make the distinction structural. Option A would be acceptable only if every consumer were forced to filter on `measure`.

**Revision model.** No revision of a published national value was observed, but a within-bulletin inconsistency (section 7) shows ZamStats tables can disagree. Use **append-only, vintage-aware** storage: unique key `(series identity, reference_month, publication_id)`; a numeric conflict between two publications for the same identity is stored as two rows and flagged; the "current" view serves a value only when all vintages agree. Identical numeric overlap collapses; `274.2` vs `274.20` is not a conflict. This is the same conflict-detecting pattern already used for BoZ rates and ERB fuel prices.

## 17. Importer/parser recommendation

- **Discovery:** parse the archive page (`/monthly-bulletin/`), extract `href` PDF links, deduplicate by volume number, order newest first; for `latest`, read only the newest bulletin; for history, a manual `backfill_cpi` importer in the pattern of the existing `backfill_*` commands, driven by an explicit, vetted manifest of volume numbers (no blind crawling), one download at a time.
- **Extraction:** `pdftotext -layout` (Poppler is already used by earlier ZM Terminal phases), then strict anchored parsing of the three annex tables and Table 5. The extraction must:
  - locate each table by caption **and** validate by header row and expected number of months;
  - assign years by counting back from the bulletin's reference month, not from the centred year label;
  - require monthly contiguity and the expected last month;
  - skip table copies in non-CPI sections (a throwaway parser mis-read one 2019 narrative row as data).
- **Validation (fail loudly):** reference month equals the bulletin's month; the annual rate in Table 1.3 equals `round_half_up(index_t / index_{t-12} - 1, 1)` and monthly the same for the previous month (all published rates passed in Vol 283; an index/rate mismatch should be reported, not repaired); Food and Non-Food indices bracket the total.
- **Tests:** small, synthetic or minimally excerpted text fixtures; no live network in tests; a fixture each for the `(x.y)` convention, `-x.y`, `274.2` vs `274.20`, a duplicated row, a missing January/February year prefix, and an incomplete table.
- **No OCR needed** for 2003 to 2026 sample files (all had a text layer); verify before any bulk import.

## 18. API and frontend recommendations (design only)

**API.** Name the resource after the source model, which is index-first: `GET /api/v1/cpi/latest` and `GET /api/v1/cpi/history`. Two routes only, as for existing verticals. The response keeps three explicit sections, never a flat list of "values":

```json
{
  "reference_month": "2026-10",
  "regime": "2009=100",
  "index": {"all_items": {"value": "564.62", "source_value_text": "564.62"}, "food": {...}, "non_food": {...}},
  "annual_inflation_pct": {"all_items": {"value": "6.3"}},
  "monthly_inflation_pct": {"all_items": {"value": "0.7"}, "food": {"value": "0.3"}, "non_food": {"value": "1.2"}},
  "source": "ZamStats", "bulletin": "Vol 283 - The Monthly - October 2026", "source_url": "...", "retrieved_at": "...", "rights_status": "review_required"
}
```

`history` accepts `measure=index|annual|monthly` and `category`, and returns the regime on every point. `INFLATION ZM` is a command alias for the same data, not a second route. Values are JSON decimal strings.

**Frontend (design only).** A dashboard card in the style of the existing ones:

```text
INFLATION ZM
6.3%
ANNUAL · OCT 2026
ZAMSTATS
```

shown only when the annual rate is source-published (Table 1.3 is). Detail view: annual headline; monthly inflation; food and non-food; the CPI index; a monthly history chart; provenance; and a methodology/base-period notice stating `2009 = 100` and that figures before 2012 use a different CPI regime. The history chart must break or flag the regime boundary and never join the 1994-base and 2009-base series; the displayed annual rate comes only from stored published values.

## 19. Unresolved risks

1. **Rights.** ZamStats commercial/redistribution terms are unclear; `review_required`. Public deployment needs written clarification.
2. **Annual Food/Non-Food** in the current layout exists only as chart labels and narrative; deriving it from the index is arithmetic-safe (all verified cases reproduce) but not a published value. Owner decision required.
3. **Layout churn** across 283 bulletins (table titles, window lengths, year labels, duplicated rows). Only 32 of 283 bulletins (about 11%) were read; a full-archive dry run is needed before backfill.
4. **Within-bulletin inconsistency** (Dec 2016, three months) and a formatting variance show tables can disagree; the conflict policy handles them but the Feb 2013, Feb 2014 and Aug 2014 values need a human decision.
5. **A future rebasing** (after the 14 years of `2009 = 100`) is plausible; the design carries the regime in the series identity so a new base is a new series, never an overwrite. A bulletin that announces a new base must stop the importer.
6. **Pre-2012 regime** is not comparable, lacks verified index levels, and the table-level monthly depth before 2008 is unverified.
7. **BoZ feed** can mislabel or omit months and mixes regimes; it must stay a cross-check only.
8. **Release timing** is stated by BoZ's feed and past behaviour, not by a ZamStats release calendar (none was found). "Latest" is defined by what is actually published.
9. **No machine-readable ZamStats endpoint** (Cloudflare-protected portals were not worked around); the project depends on PDF layout stability.
10. **"Northern/Muchinga"** province merge and the 9-column provincial table are an unresolved modelling point for Phase 2.

## 20. Implementation decision

**Zambia CPI/inflation source research complete; Phase 1 approved for local/internal implementation** (national monthly CPI index for All Items, Food and Non-Food; annual overall inflation; monthly overall, food and non-food inflation; regime `2009 = 100` only; rights `review_required`). Prerequisites that belong to the implementation task, not this one:
- a manifest-driven `backfill_cpi` importer and a latest-only importer, strict text parsing with the validation in section 17;
- a vintage-aware migration for `cpi_publications`, `cpi_index_observations` and `cpi_inflation_observations` (not created here);
- stored-only `GET /api/v1/cpi/latest` and `/history`;
- the owner's decision on derived annual Food/Non-Food (section 19, item 2);
- no public deployment until the ZamStats Legal Department confirms the terms.

Future analytics that CPI unlocks, **not** implemented and to remain separate derived analytics: `REAL RATE ZM`, inflation versus the BoZ policy rate, inflation versus T-bill yields, inflation-adjusted returns, and `MISERY ZM` once labour data exists.
