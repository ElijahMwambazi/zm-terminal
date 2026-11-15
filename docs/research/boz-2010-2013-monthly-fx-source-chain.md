# BoZ official monthly USD/ZMW, 2010-01 to 2013-12: source chain

Status: **research only, no code, schema or data change.** Companion to `docs/research/boz-deeper-fx-history.md` (which found the Annual Report extension and the ÷1,000 evidence at a high level) and to the phase-1 implementation (Fortnightly Statistics Table 02, 2014-01 onward). This document pins down the 2010-2013 chain month by month so a phase 2 can be implemented without further discovery.

Research date: 2026-11-15. Only public, anonymous Bank of Zambia (BoZ) endpoints were used: about 15 GETs (JSON:API listings plus 7 file downloads, each file once), sequential, at least 2 s apart, descriptive `User-Agent`, no redirects, no authentication. BoZ was not contacted in any other way. Downloaded files were kept in a session scratch area and are **not committed**.

## 1. Verdict

**Every month 2010-01 to 2013-12 (48 months) is published by BoZ in its own Annual Reports, Table 13 "Kwacha/US Dollar Exchange Rates, Monthly Average" (Bank of Zambia rates: buying, selling, mid), and every month appears in at least two of the five reports reviewed for this research, with identical values.** Scope: the five reports are the selected, reviewed source chain, not an exhaustive list of BoZ publications that print the table (see section 2). The chain connects to the implemented phase-1 series: the 2014 Annual Report prints 2014-01 to 2014-12 and all 12 match the stored Fortnightly-derived rows exactly (0 differences).

Two things must be handled by a phase 2, both already anticipated by the earlier research:

1. **Units.** 2010-01 to 2012-12 are published in old kwacha (ZMK scale, about 4,500 to 5,300); 2013-01 onward in rebased kwacha (about 5.3 to 5.5). The table has no unit label. The boundary is fixed by observation month (see section 5), never by magnitude.
2. **Two published ordering violations** (`buy <= mid <= sell`): 2012-04 and 2013-12 (section 6). No other month in 2010-2013 violates the rule.

Rights status is unchanged: `review_required`, local/internal use only; no licence text was found and BoZ was not asked.

## 2. Sources found, and what each is worth for 2010-2013

| Source | What it holds for 2010-2013 | Use |
|---|---|---|
| **Annual Reports 2010, 2011, 2012, 2013, 2014**, Table 13 | Official **monthly average** buy/sell/mid (BoZ and Bureau), 2 decimals; section 3 | **The chain.** |
| Fortnightly Statistics archive (254 files) | Starts 2016; the earliest issue's monthly block begins 2014-01 | Phase 1 only; nothing earlier |
| Financial and Other Statistics booklets (listing: 2014 to 2025) | Nothing before 2014; 2014 booklet is scanned images (earlier research) | None |
| `FORTNIGHTLYTIMESERIESending30October2026.xlsx` (series "Monetary and Financial Statistics") | Sheet `K-USD Exchange Rates`: **daily** buy/sell/mid from 2001-01-02, already in rebased units; sheet `Table 02`: weekly averages from 2013; no monthly block | Cross-check only; defective for some months (section 7) |
| `QuarterlySelectedMacroeconomicIndicators` workbooks | Quarterly indicators, single sheet | None |
| Annual Reports 2004-2009 and 2015-2025; National Payment Systems reports | Not reviewed in this pass, except that the 2015 report's Table 13 is known (from review) to repeat the 2013 months; earlier research saw Dec-only rows and a 1-decimal 2025 table | Not part of the selected chain |

The BoZ Annual Report listing has 21 BoZ Annual Reports (2004 to 2025, no 2006) plus 14 National Payment Systems reports, 35 nodes in all; five of them (2010 to 2014) were reviewed and selected for this chain. The set is **not exhaustive**: a later review found that the 2015 Annual Report, Table 13 (PDF page 144, printed page 136), also publishes all twelve 2013 months (identified in review; not re-extracted or compared in this pass). Other BoZ publications may likewise reproduce the same historical table. The chain below is a deliberately selected earliest-publication chain, not a claim of global source exclusivity, and the 2015 report is not part of the proposed canonical persistence sources.

## 3. The chain

Base URL `https://www.boz.zm`. Table 13 is a landscape page. Printed page numbers are the numbers on the page; PDF pages count from the cover.

| Report | Path | PDF page (printed) | Rows 2010-2013 | Units | Notes |
|---|---|---|---|---|---|
| 2010 Annual Report | `/sites/default/files/migrated/annual-reports/2010/BOZAnnualReport2010.pdf` | 148 (140) | 2010-01 to 2010-12 | ZMK | Heading reads "BANK OF ZAMBIA AND BUREAUX KWACHA/US DOLLAR EXCHANGE RATES"; Dec rows 1995-2007, monthly 2008-2010 |
| 2011 Annual Report | `.../annual-reports/2011/BOZANNUALREPORT2011.pdf` | 141 (133) | 2010-01 to 2011-12 | ZMK | |
| 2012 Annual Report | `.../annual-reports/2012/BOZANNUALREPORT2012.pdf` | 145 (137) | 2010-01 to 2012-12 | ZMK | Dec rows 1995-2009 then monthly 2010-2012 |
| 2013 Annual Report | `.../annual-reports/2013/BOZANNUALREPORT2013.pdf` | 146 (138) | 2010-12 (Dec row), 2011-01 to 2013-12 | ZMK to 2012-12, ZMW from 2013-01 | The regime changes inside one column |
| 2014 Annual Report | `.../annual-reports/2014/BOZANNUALREPORT2014.pdf` | 139 (131) | 2010-12, 2011-12 (Dec rows), 2012-01 to 2013-12, plus 2014-01 to 2014-12 | ZMK to 2012-12, ZMW from 2013-01 | Bridges to phase 1 |

Canonical provenance if the existing rule is kept (earliest publication that contains the month): 2010 months from the 2010 report, 2011 months from the 2011 report, 2012 months from the 2012 report, 2013 months from the 2013 report. Corroborating reports per month: 2010-01 to 2010-11 two more (2011, 2012), 2010-12 four more, 2011-01 to 2011-11 two more, 2011-12 three more, 2012 two more (2013, 2014), 2013 one more (2014). Histogram of publications per month over the 48 months: 2 publications: 12 months (2013), 3: 34, 4: 1 (2011-12), 5: 1 (2010-12).

File fingerprints (SHA-256) of the copies read, for pinning a manifest:

| File | SHA-256 |
|---|---|
| BOZAnnualReport2010.pdf (9,320,554 B) | `752e35fe36704a4b293a2bf6717b5f32db1f63436e34d80f99de425554df2610` |
| BOZANNUALREPORT2011.pdf (9,040,649 B) | `9dfea388de98718201e345534b830688dec554c00273f484e7e218c911135ac5` |
| BOZANNUALREPORT2012.pdf (7,670,856 B) | `fe64384f43cdb7a58df8fc2df4977886ff5366d0d49ab5d548152f000221f424` |
| BOZANNUALREPORT2013.pdf (6,415,553 B) | `9c85f2cfb3a8bd141dc70362e087ff4c1975cc4cbb63e1740104d1780bddc645` |
| BOZANNUALREPORT2014.pdf (13,303,623 B) | `e97747fb6f960b509355d6b69461069bb4ae7a4ffd5a91a9bca985e5bdd78209` |

Boundary rows, as published (buying / selling / mid; ZMK for 2010-2012): 2010-01 `4,504.49 / 4,524.49 / 4,513.99` (2010 report); 2012-12 `5,198.47 / 5,228.97 / 5,208.46`; 2013-01 `5.27 / 5.29 / 5.28`; 2013-12 `5.52 / 5.53 / 5.54`. The full 48-row extraction was kept in the scratch area, not committed.

## 4. Consistency results

- **Overlaps between reports, all differing triples counted across buy/sell/mid and the Bureau columns:** 2010 vs 2011: 12 shared months, 0 differences; 2010 vs 2012: 12, 0; 2011 vs 2012: 24, 0; 2011 vs 2013: 13, 0; 2012 vs 2013: 25, 0; 2012 vs 2014: 14, 0; 2013 vs 2014: 26, 0; 2010 vs 2013: 1, 0. **No revisions between reports were observed.** This is a finite sample of five reports and is not evidence that BoZ never revises.
- **Bridge to phase 1:** 2014 report months 2014-01 to 2014-12 versus the 12 stored phase-1 rows: 0 differences in buy, sell and mid.
- **Completeness:** the union of the five reports covers all 48 months, no gaps.
- **Midpoint:** the published mid differs from `(buy + sell) / 2` in 11 of 48 months (for example 2010-01 by -0.50 ZMK, 2012-04 by -26.315, 2012-12 by -5.26). As in phase 1, midpoint arithmetic is a diagnostic only, never a validity rule and never a replacement value.
- **Precision:** 2 decimals as printed (ZMK for 2010-2012, ZMW for 2013).

## 5. Units and the 2013 break

Official evidence for ÷1,000 and its date is in `boz-deeper-fx-history.md` section 9 (2012 Annual Report p. 72; 2013 Annual Report pp. 68, 91). New corroboration found here, from BoZ's own data: the time-series workbook's `K-USD Exchange Rates` sheet publishes 2001-2012 daily rates already in rebased units, and its monthly means agree with Table 13 divided by 1,000 (for example 2010-12: mean 4.7357 versus 4,735.74 ZMK; 2012-12: 5.195 versus 5,208.46). That is independent evidence that the ÷1,000 reading of the old rows is right, though it does not by itself fix the rule for a table.

Rule for any future conversion (unchanged from Gate 4 of the earlier report, restated for this chain):

- Regime is fixed per publication, table and **observation month**: months before 2013-01 in Table 13 of every report listed in section 3 are old kwacha; 2013-01 onward are rebased. Never by magnitude, never by report date.
- The 2014 report's table prints 2012-12 `5,208.46` and 2013-01 `5.28` one under the other with no unit label, so the month rule is the only safe discriminator.
- Normalized ZMW value is the printed value ÷ 1,000 as an exact decimal shift (`4,513.99` becomes `4.51399`: five decimals). Original printed value, regime, rule and normalized value must all be stored (Gate 4).

Implication for the schema (no change made): the current `fx_monthly_averages` table has `currency_regime CHECK = 'ZMW'` and `published_precision`, and stores one value per column. It cannot hold an original ZMK value or a conversion rule. A phase 2 needs an additive migration or a separate table carrying original values, `original_currency_regime`, `conversion_rule` and the normalized values; the API contract and existing rows must not change.

## 6. Ordering exceptions in 2010-2013

Only ordering violations are validity failures. In this window there are exactly two, both published and repeated across reports:

| Month | Reports that print it | Published buy / sell / mid | Violation |
|---|---|---|---|
| 2012-04 | 2012 (p.145), 2013 (p.146), 2014 (p.139) | 5,283.60 / 5,250.97 / 5,240.97 | selling < buying; mid < selling; mid < buying |
| 2013-12 | 2013 (p.146), 2014 (p.139) | 5.52 / 5.53 / 5.54 | mid > selling |

Both are identical in every report that prints them. Observed while implementing phase 2: the 2010 report's Table 13 also prints 2008-08 with mid above selling (outside the 2010-2013 window, not an approved exception and never persisted), so ordering is enforced from each report's persistence or validation window onward and earlier rows of the page are structure-checked only. A phase 2 would carry a named exception list (month, rule, exact values, publication, page, reason) exactly as `ORDERING_EXCEPTIONS` in the phase-1 parser anticipates, and store the value as published ("accepted because BoZ published it", not "corrected"). The other 46 months satisfy `buy <= mid <= sell`.

## 7. Why not derive months from daily data

The workbook's daily sheet looks like an easier source and is not one:

- Monthly means of its daily mids agree with the published monthly mid within 0.01 ZMW in 42 of 48 months, but the maximum difference is **0.30 (2011-06)**, and 2012-07 (0.026) and 2012-09 (0.062) are also off.
- The 2011-06 gap is a **workbook defect**: its 22 daily values for June 2011 are identical, day for day, to June 2010 (first values 5.023, 5.0518, 5.0674, 5.1214, ...).
- BoZ's monthly-average methodology (trading days, weighting, rate used) is not published, so a computed mean would be a different, unofficial series.
- The workbook has no unit label and mixes units across sheets.

Use the workbook only as a sanity check, never as a source of stored months.

## 8. Extraction notes

- **The existing parser cannot read these pages.** Table 13 is a rotated landscape page. `pdfplumber` (used in phase 1) returns the text layer as one reversed token per line (for example `rebmeceD` and `61.749`), so the Table 02 line-based approach does not apply.
- **`pdftotext -layout` (poppler 25.07) reads the pages correctly**: `pdftotext -layout -f <pdf page> -l <pdf page> <file> -`. With a line pattern of `[year] <Month> <six numbers>`, the five pages yielded 49 (2010), 61 (2011), 51 (2012), 47 (2013) and 43 (2014) rows, including December-only rows and 2014 months.
- Poppler is a system binary, a new runtime dependency. The alternative is rebuilding rows from `pdfplumber` characters using their rotation matrix: pure Python, but more work and untested here. This is a product decision for phase 2.
- Row shape differs from Table 02: year context is given in one of two vetted ways: an inline year label on the first month row of each year (or on every December-only row), or, as observed in the 2010 report, a standalone year line that sets the year for the month rows that follow; then, per row, `<Month> buy sell mid bureau-buy bureau-sell bureau-mid`. Column order is Buying, Selling, Mid. Thousands separators appear for ZMK rows (`4,504.49`). The 2010 report adds "AND BUREAUX" to the heading. A future extractor must support both year-context forms for these vetted pages only, keep explicit table and page identity checks (heading, `Table 13`, column labels, expected PDF page), and fail closed if the year context is ambiguous (for example a year line with no following month row, two consecutive year lines, or a month row with no year context); it must not guess other layouts.
- Text-bearing PDFs; no OCR was needed for these five pages.

## 9. Risks and open items

- **Rights.** Unchanged: `review_required`; local/internal use only.
- **Units without labels.** The rule in section 5 depends on official rebasing evidence, which is solid, but the table itself never states its unit. Parsing must pin each report and page and fail if a row's regime falls outside the rule.
- **Ordering exceptions.** Two accepted violations require named exceptions; any other violation must still fail.
- **Methodology.** Monthly-average method is not published; mid is stored as published.
- **Report revisions.** None observed across five reports; not provable for the future. Storage must stay conflict-aware.
- **Partial verification.** The 2010 and 2011 reports were compared against later reports only; within the reports reviewed here, the only second print of the 2013 months is the 2014 report (the 2015 report also prints them, per review, but was not compared). Later reports are later editions of the same BoZ table, not independent measurements.
- **Page drift.** Table 13 moved between reports (printed pages 140, 133, 137, 138, 131); a manifest must pin the PDF page and fail on layout change.

## 10. Recommended phase 2 (not implemented)

1. Manifest of the five reports above with pinned PDF page, expected first/last month and SHA-256 (or size) check; HTTPS `www.boz.zm` only, one GET per file, sequential, no redirects, as in phase 1.
2. Parse 2010 to 2013 (and Dec rows only as needed for cross-checks) with the line pattern in section 8; verify 48 months, no gaps, ordering rule with the two named exceptions, and all overlaps identical before any write; use the 2014 report's 2014 rows as the bridge check against stored phase-1 data.
3. Store original printed value, original regime, the `÷1,000` rule id and the normalized ZMW value with provenance; additive schema only; the existing `fx_monthly_averages` API and rows stay untouched.
4. Expected result: 48 additional months, `2010-01` to `2013-12`, 0 gaps, contiguous with phase 1 (`2014-01`), for 201 months in total.
5. Rights review first if the data is to leave local use; then, separately, the planned `copper_zm_v2` and frontend extensions.
