# BoZ deeper USD/ZMW history: source discovery

Status: **research only**. Nothing in this document is implemented. No provider, migration, schema, route or Copper methodology change was made, and Bank of Zambia (BoZ) was not contacted. Research dates: retrieved 2026-11-14/15 from public, anonymous BoZ endpoints; every candidate file was downloaded once with a descriptive `User-Agent`, requests were spaced at least 2 s apart and the whole investigation was about 40 requests.

## 1. Objective

Find out whether ZM Terminal can defensibly extend official USD/ZMW history earlier than the intraday JSON boundary of `2026-01-22`, with the **monthly average** series as the main target (Copper and other monthly analytics), without inventing timestamps, converting monthly data into daily rows, mixing definitions, mishandling the 2013 rebasing, adding undocumented calculations, or scraping unstable sources.

## 2. Current limitation

- The live adapter and `fx_rates` hold timestamped intraday observations (about 09:30 / 12:30 / 15:30 Africa/Lusaka) from `boz_zmw_usd_daily_exchange_rates`, which serves nothing before 2026-01-22. Stored: 492 rows, 2026-01-22 to 2026-11-13.
- Copper analytics (`COPPER ZM`, `copper_kwacha_corr_v1`, sensitivity) use the project's observed-days monthly FX average, so the matched sample is limited to 2026 (production-method sample replicated in section 16: 9 usable level months, February to October; 8 transition pairs; change endpoints March to October).
- This research does not change that dataset. Intraday history and any monthly series must stay separate datasets.

## 3. Sources investigated

| # | Source | Access | Outcome |
|---|---|---|---|
| 1 | Average Exchange Rates page and its JSON:API nodes (`exchange_rates`, `historical_average_exchange_rate`) | anonymous | daily series; see sections 4 and 5 |
| 2 | "Download historical rates" workbook behind that page (`AVERAGE_FXRATES_*.xlsx`) | anonymous | daily buy/sale, 2006 to date, ZMK to ZMW break unlabelled |
| 3 | Fortnightly Statistics PDF archive (254 files listed, 2016 to 2026; 14 downloaded) | anonymous | **monthly buy/sell/mid from 2014-01**; section 6 |
| 4 | Annual Reports 2012, 2013, 2025 (3 of 35 listed) | anonymous | monthly from 2010-01 (old kwacha) and 2013; section 7 |
| 5 | Financial and Other Statistics booklets (2025 downloaded, 2014 inspected, 13 listed) | anonymous | end-period only / scanned images; section 7 |
| 6 | "Daily USD/ZMW time series" workbook (`DAILY_RATES_*.xlsx`), the previously rejected file | anonymous | intraday analyst workbook; section 8 |

## 4. Average Exchange Rates page

`https://www.boz.zm/markets-securities/average-exchange-rates` is an Angular single-page app (static `index.html`, 9.9 KB; `robots.txt` is the SPA fallback page, so no robots policy was served). Its public `main.*.js` bundle shows the data paths it calls, all anonymous and under `https://www.boz.zm`:

- `GET /jsonapi/node/exchange_rates?include=field_average_exchange_rates&sort=-created&page[limit]=1`: the latest **daily** "AVERAGE EXCHANGE RATES 1530HRS dd mm yy" node with USD, GBP, EUR and ZAR `buying` and `selling` strings (4 decimals, for example USD `20.3998` / `20.4498` on 2026-11-12). No mid is published; it is derivable only. The earliest node in this content type is **2026-10-07**, so this API holds about five weeks of history.
- `GET /jsonapi/node/historical_average_exchange_rate`: 33 nodes (2026-09-30 to 2026-11-13, one per working day), each pointing to one uploaded workbook through `field_average_historical_file`; resolved through `/jsonapi/file/file/<uuid>` to a URL such as `/sites/default/files/2026-11/AVERAGE_FXRATES_8.xlsx` (622,181 bytes, last modified 2026-11-13).
- The "Download historical rates" button downloads that newest workbook. The file URL changes on every upload (`AVERAGE_FXRATES_<n>.xlsx` under a month folder), so the stable entry point is the JSON:API node list, not a file URL.

## 5. Structured download and API findings

The workbook (single sheet ` Media Rates Series`, with a leading space in the name; saved by BoZ staff, internal path in `workbook.xml` points at an FX "Media Rates" folder) is **a daily series, not a monthly one**:

- Row layout: `Date | Dollar Buy | Dollar Sale | Pound Buy/Sale | Euro Buy/Sale | Rand Buy/Sale`. Buy and sale only; **no mid**.
- Coverage: 5,179 dated rows from **2006-01-03** to 2026-11-13, no duplicates, strictly increasing. Dates are Excel serials; rows from about 2025 onward are formulas (`=B3049+5`, 1,575 formula cells), not stored dates.
- **Six rows inconsistent with the declared column structure** (sheet rows 6 to 11, dates 2006-01-04, -05, -06, -09, -10, -11; the neighbouring rows 2006-01-03 and 2006-01-12 are normal). Header layout: B `Date`, C/D Dollar Buy/Sale, E/F Pound, G/H Euro, I/J Rand. In the six rows:
  - Dollar buy (C) holds `43649`, `43650` or `43651` and Dollar sale (D) holds `0.6458...`, `0.3958...` or `0.5208...` (for example row 6: `43649` / `0.64583333333333337`), against ZMK-scale values of about 3,200 to 3,500 on the adjacent normal rows;
  - Pound buy (E) holds about `12.79` to `12.88` (normal rows about 5,700 to 6,000) and Pound sale (F) holds values between `-0.287` and `0.133`;
  - Euro buy/sale (G/H) hold about `12.7` to `13.0` (normal rows about 3,900 to 4,200);
  - the text `Stable` and `Stay Out` sit in the **Rand** columns I/J, not the Dollar columns.

  These rows are internally inconsistent with the workbook's declared column structure and fail the project's validation rules (positive numeric values in the expected magnitude, sale at or above buy). Provenance of the values is not known and this document does not claim how they got there. A parser would have to detect and exclude them, which is a recorded, named exception rather than a repair.
- Values are Excel binary floats (for example `5.2062962962962951`), with 15 to 17 significant digits that are not source precision. The 4-decimal JSON strings agree with the workbook row for the same day (2026-11-12: `20.3998` vs `20.39980238...`).
- **Currency eras are unlabelled in the file.** The same column holds ZMK-scale values until 2012-12-28 (`5136.61`) and rebased values from 2013-01-02 (`5.206...`), with no unit header, note or era marker.
- Extra observation (not a documented definition): for 2026-11-12 the three stored intraday buys (20.3931, 20.4000, 20.4063) average to 20.3998, exactly the published daily "average" buy. The daily average therefore looks like the mean of that day's intraday observations. This is numerical agreement only. **Methodology not established.**

Request discipline: anonymous GET only, no authentication or bypass, JSON:API and files are static-like and cacheable. A future importer would need one list request and one file request per run; no rate problem expected, but BoZ has published no API terms.

## 6. Fortnightly statistical publications

Listing: `GET /jsonapi/node/statistics?filter[field_series.id]=<Fortnightly Statistics term>&include=field_statistic_files` (six pages of 50) returns 254 PDFs: 1 in 2016, 29 in 2017, then about 26 a year through 2026 (22 in 2026). 14 were downloaded: 2016 `Vol24No01`, 2017 `Vol25No6`, 2019, 2020, 2021, 2022, 2023, 2024 (issue 26 of each), 2025 issues 14 and 26, 2026 issues 8, 14, 21 and 22.

- Table 02 `KWACHA/US DOLLAR EXCHANGE RATES` (also summarised in Table 01A `BANK OF ZAMBIA MID-RATES`, with `End of Period` and `Period Average` K/USD) gives **Bank of Zambia Rates buying / selling / mid-rate** and Bureau Rates, under "Monthly Averages", then weekly averages and recent daily values. Text layer present; the heading and column-label layout varies by era (section 6.1).
- Each issue carries a rolling window of 12 to 36 monthly rows.
- **Earliest month**: 2014-01, in the earliest listed file (`StatisticsFortnightly2016Vol24No01.pdf`, whose contents run to December 2016, so its label does not match its contents). **Latest**: 2026-09 (issue 21, 2026-10-27); the one-page issue 22 carries no table.
- **Coverage wording.** The reconstructed series from the selected publications contains every calendar month from 2014-01 through 2026-09 (153 months). This does not mean every fortnightly issue in the period was individually parsed: 13 of the 254 listed issues were parsed (the 14th downloaded file, 2026 issue 22, has no table).
- **Precision: 2 decimals** in every period (Table 02 and 01A).
- Same-definition check: Table 02 monthly mid equals Table 01A `Period Average`.
- Published anomalies are listed with exact values and page references in section 6.2 (2022-11 prints buying 16.16, selling 16.57, mid 16.55; a research arithmetic check also flags 2019-03).
- A note printed under the neighbouring real-effective-exchange-rate table in the 2025 issue says revisions were made to 2016 figures in March 2017 and to figures from 18 to 29 December 2023 on 19 January 2024; its scope is not stated, and no monthly change was observed (section 12).
- The `Volume` label scheme and file names are irregular (`Vol25No6`, `IssueNo_26`, `IssueNo_13_1`, `_0` suffixes), so discovery must go through the JSON:API list, not constructed URLs.

### 6.1 Exact source evidence for Table 02

All 13 text-bearing issues carry Table 02 on **PDF page 3** (24 to 27 pages per issue). Base URL `https://www.boz.zm`; file paths as listed by the JSON:API statistics list (titles as listed):

| Issue (listed title) | File path | Months in its monthly block |
|---|---|---|
| Volume 24 Issue No. 01 - 2016 | `/sites/default/files/migrated/fortnight-statistics/2016/StatisticsFortnightly2016Vol24No01.pdf` | 2014-01 to 2016-12 |
| Volume 25 Issue No. 06 - 2017 | `/sites/default/files/migrated/fortnight-statistics/2017/StatisticsFortnightly2017Vol25No6.pdf` | 2016-01 to 2018-02 |
| Volume 26 Issue No. 26 - 2019 | `/sites/default/files/migrated/fortnight-statistics/2019/StatisticsFortnightly2019Vol26No26.pdf` | 2017-01 to 2019-11 |
| Volume 28 Issue No. 02 - 2020 | `/sites/default/files/migrated/fortnight-statistics/2020/StatisticsFortnightly2020Vol28No02.pdf` | 2018-01 to 2020-12 |
| Volume 28 Issue No. 27 - 2021 | `/sites/default/files/migrated/fortnight-statistics/2021/StatisticsFortnightly2021Vol28No27.pdf` | 2019-01 to 2021-12 |
| Volume 29 Issue No. 26 - 2022 | `/sites/default/files/migrated/fortnight-statistics/2022/StatisticsFortnightly2022Vol29IssueNo26.pdf` | 2020-07 to 2022-12 |
| Volume 30 Issue No. 26 - 2023 | `/sites/default/files/migrated/fortnight-statistics/2023/StatisticsFortnightly2023Vol30IssueNo26.pdf` | 2021-07 to 2023-12 |
| Volume 31 Issue No. 26 - 2024 | `/sites/default/files/migrated/fortnight-statistics/2024/StatisticsFortnightly2024Vol31IssueNo26.pdf` | 2022-01 to 2024-11 |
| Volume 32 Issue No. 14 - 2025 | `/sites/default/files/migrated/fortnight-statistics/2025/StatisticsFortnightly2025Vol32IssueNo_14.pdf` | 2024-01 to 2025-06 |
| Volume 32 Issue No. 26 - 2025 | `/sites/default/files/migrated/fortnight-statistics/2025/StatisticsFortnightly2025Vol32IssueNo_26.pdf` | 2024-01 to 2025-11 |
| Fortnightly Statistics Volume 33 Issue No. 08 (2026-04-28) | `/sites/default/files/2026-04/StatisticsFortnightly2026Vol33IssueNo_8.pdf` | 2024-01 to 2026-03 |
| FORTNIGHTLY STATISTICS (2026-07-21; file `...Vol33IssueNo_14`) | `/sites/default/files/2026-07/StatisticsFortnightly2026Vol33IssueNo_14.pdf` | 2024-01 to 2026-06 |
| FORTNIGHTLY STATISTICS (2026-10-27; file `...Vol33IssueNo_21`) | `/sites/default/files/2026-11/StatisticsFortnightly2026Vol33IssueNo_21.pdf` | 2024-01 to 2026-09 |

Not used for months: `/sites/default/files/2026-11/StatisticsFortnightly2026Vol33IssueNo_22.pdf` (listed 2026-11-10; 1 page, no Table 02). The listed 2016 and 2017 issues are labelled by their listing year but their monthly blocks start earlier (2014-01 and 2016-01); the label is not a reliable indicator of coverage.

On PDF page 3 of each file the block identifiers are `Table 02`, the heading `KWACHA/US DOLLAR EXCHANGE RATES`, and `Monthly Averages`. The layout varies by era, which matters for a parser: in the 2024 to 2026 issues `Table 02` and the heading share a line and `Monthly Averages` is on one line; in the 2017 to 2023 issues the words `Monthly` and `Averages` are split across two lines; in the 2016, 2017 and 2019 issues the heading is on a different line from `Table 02`. Year labels are separate lines above groups of month rows. Columns on the pages inspected are BoZ buying, selling, mid, then Bureau buying, selling, mid. These layout differences are exactly what Gates 2, 3 and 8 are for.

Representative values (all on PDF page 3 of the named file):

| Month | Buying | Selling | Mid | Issue |
|---|---|---|---|---|
| 2014-12 | 6.33 | 6.35 | 6.34 | 2016 Vol 24 No 01 |
| 2022-11 | 16.16 | 16.57 | 16.55 | 2022 Vol 29 No 26 (also 2023 and 2024 issues) |
| 2025-08 | 23.22 | 23.27 | 23.25 | 2026 Vol 33 No 21 (also issues 8 and 14, and 2025 No 26) |

### 6.2 Published anomalies (exact values)

These are values as published; none is repaired, recomputed or swapped.

| Month | Publication | Page / table | Buying | Selling | Mid | Observation |
|---|---|---|---|---|---|---|
| 2012-04 | `BOZANNUALREPORT2012.pdf`, `/sites/default/files/migrated/annual-reports/2012/BOZANNUALREPORT2012.pdf` | PDF p. 145 (printed 137), Table 13 | 5,283.60 | 5,250.97 | 5,240.97 | selling is below buying; the printed mid is also below both |
| 2012-04 | `BOZANNUALREPORT2013.pdf`, `/sites/default/files/migrated/annual-reports/2013/BOZANNUALREPORT2013.pdf` | PDF p. 146 (printed 138), Table 13 | 5,283.60 | 5,250.97 | 5,240.97 | identical to the 2012 report |
| 2013-12 | `BOZANNUALREPORT2013.pdf` (URL above) | PDF p. 146 (printed 138), Table 13 | 5.52 | 5.53 | 5.54 | printed mid is above selling |
| 2022-11 | `StatisticsFortnightly2022Vol29IssueNo26.pdf` (also `2023Vol30IssueNo26`, `2024Vol31IssueNo26`) | PDF p. 3, Table 02 `Monthly Averages` | 16.16 | 16.57 | 16.55 | printed mid is far from `(buy + sell) / 2` (16.365) and the neighbouring spread is about 0.05 |
| 2019-03 | `StatisticsFortnightly2019Vol26No26.pdf` (also `2020Vol28No02`, `2021Vol28No27`) | PDF p. 3, Table 02 `Monthly Averages` | 12.01 | 12.04 | 12.04 | found by a research arithmetic check only: mid equals selling and exceeds `(buy + sell) / 2` (12.025) by more than 2-decimal rounding of buy and sell can explain |

The first three annual-report rows and the two fortnightly rows were found by checking the 201-month chain for `buy <= mid <= sell` and for the mid being within printing resolution of `(buy + sell) / 2`. The check is a research arithmetic test, not a BoZ-defined rule, and the list is the result of one pass over the sampled issues, so it must not be treated as exhaustive.

PDF parsing is **not** implemented; this is a viability finding only. Viability: viable, with a fail-loud parser pinned to Table 02, because Table 02 was on PDF page 3 in all 13 issues and one regex recovered all 153 months, although the heading and column-label layout differs by era (section 6.1), so one layout cannot be assumed; the risks are layout changes and the published anomalies (section 18).

## 7. Annual reports and statistical booklets

Annual Reports (the listing has 35 publication nodes, which include the National Payment Systems annual reports; BoZ Annual Reports run 2004 to 2025 with no 2006 report):

| Report | Table | Content | Precision |
|---|---|---|---|
| 2012 (`BOZANNUALREPORT2012.pdf`; PDF p. 145, printed 137) | Table 13 `KWACHA/US DOLLAR EXCHANGE RATES`, "Monthly Average" | **December rows 1995 to 2009, then every month 2010-01 to 2012-12**, buy/sell/mid for BoZ and Bureau, in **old (ZMK-scale) kwacha** (for example Dec 2012 mid `5,208.46`) | 2 decimals of ZMK |
| 2013 (`BOZANNUALREPORT2013.pdf`; PDF p. 146, printed 138) | Table 13 `KWACHA/US DOLLAR EXCHANGE RATES`, "Monthly Average" | December rows 2000 to 2010, monthly 2011 to 2012 (old units, identical to the 2012 report: 35 overlapping rows, 0 differences), monthly **2013 in rebased units** (Jan `5.28`) | 2 decimals |
| 2025 (`Bank of Zambia Annual Report_2025.pdf`, `/sites/default/files/2026-06/Bank%20of%20Zambia%20Annual%20Report_2025.pdf`; PDF p. 196, printed 180) | Table 10 `Kwacha/US Dollar Exchange Rates, (Monthly Average) Dec. 2014 - Dec. 2025` | December rows 2014 to 2023, then every month 2024 and 2025 | **1 decimal** |

- Annual-report file paths: 2012 `/sites/default/files/migrated/annual-reports/2012/BOZANNUALREPORT2012.pdf`; 2013 `/sites/default/files/migrated/annual-reports/2013/BOZANNUALREPORT2013.pdf` (base `https://www.boz.zm`). The 2012 table's unit regime is old kwacha throughout. The 2013 table repeats 2000 to 2012 rows in old units and then lists 2013 rows in rebased units, with no unit label on the table itself.
- The 2025 report is **not** a continuous monthly history: before 2024 it holds one December value per year (10 rows, 2014 to 2023) followed by monthly rows 2024-01 to 2025-12 (24 rows).
- **Precision comparison, recomputed from source values** (BoZ-rates columns only: buying, selling, mid kept separate; each AR2025 Table 10 value compared with the same month and field in the fortnightly Table 02 union; Bureau-rate columns excluded). 34 AR2025 months (2014-12; 2015-12 to 2023-12; 2024-01 to 2025-12), all present in the fortnightly union, so **102 comparisons** (34 months x 3 fields):
  - **5 exact matches** (numerically equal, for example 2017-12 buying `10.00` vs `10.0`);
  - **91 equal to the fortnightly value rounded half-up to 1 decimal**;
  - **6 rounding-compatible differences** where the fortnightly value is an exact `.x5` tie and the annual report prints the lower neighbour (absolute difference 0.05): 2014-12 selling (`6.35` vs `6.3`), 2015-12 selling (`10.85` vs `10.8`), 2024-04 mid (`25.35` vs `25.3`), 2025-05 mid (`26.95` vs `26.9`), 2025-06 mid (`24.55` vs `24.5`), 2025-08 mid (`23.25` vs `23.2`);
  - **0 incompatible differences** (none with an absolute difference above 0.05).
  
  Two spot checks, with fields kept separate (fortnightly values: PDF p. 3, Table 02, section 6.1; AR2025 values: PDF p. 196, Table 10). **December 2014:** fortnightly buy `6.33`, sell `6.35`, mid `6.34`; AR2025 buy `6.3`, sell `6.3`, mid `6.3` (buy and mid are half-up roundings; sell is the tie). **August 2025:** fortnightly buy `23.22`, sell `23.27`, mid `23.25`; AR2025 buy `23.2`, sell `23.3`, mid `23.2` (buy and sell are half-up roundings; mid is the tie). An earlier draft of this document gave a different count (6 of 93), mislabelled the December 2014 value as a mid and the August 2025 value as a sell, and missed Oct to Dec 2025; it is superseded by the counts above. Rounding-compatible means only that the 1-decimal print could derive from an unpublished finer value; it does not establish how BoZ rounds.
- "December" rows in these tables are December **monthly averages**, not end-of-period rates (Dec 2014 mid 6.34 in the fortnightly table, 6.3 in the 2025 report; the end-of-period rate is a different column in Table 01A).
- Booklets (`FINANCIAL AND OTHER STATISTICS`, 2014 to 2025): the 2025 booklet (52 pages) has `Table 16/17 ... End Period, 2016 - 2025` only, i.e. annual end-period values (category E); the 2014 booklet is a 36-page PDF with no text layer (scanned images; not machine-readable without OCR); not inspected further.
- Data-quality prints in the annual tables (2012-04 and 2013-12) are listed with exact values and pages in section 6.2.
- The original research brief expected 4-decimal annual-report tables; the 2025 Annual Report table is 1 decimal. Four-decimal annual figures exist only in narrative text (for example `K5.3914/US$`, the 2013 annual average).

## 8. Reassessment of the previously rejected XLSX

The file the project rejected is **not** the average-rates workbook. It is `DAILY_RATES_*.xlsx`, published through `/jsonapi/node/historical_daily_zmw_usd` (the Daily USD/ZMW time-series page), 2.4 MB, with:

- three sheets: hidden `Table1` (Power Query table plus connection), `Interbank Average Rates` (18,739 rows; intraday date/time/buy/mid/sell from **2011-09-22** to 2026-11-13) and `Retail Average Rates` (from 2025-09-15);
- a pivot table, a chart, a cached pivot of 8,553 rows and an external-data connection;
- one `#VALUE!` cell (a `Volatility` column that is also analyst-added), whitespace-padded date cells and **dates only on the first row of each day** (the following rows are blank, so a date has to be forward-filled);
- Excel-float values and ZMK-scale values (`4939.0625`) before rebasing next to rebased ones, again with no unit label.

Verdict: **keep rejected**. It is the intraday analyst workbook, not a monthly series, and safe extraction would require inference (date fill-down, time-zone assumption, era scaling, skipping analyst columns). The separate average-rates workbook is cleaner (one sheet, explicit dates, no formula errors, but six rows inconsistent with its column structure, section 5) but is a daily buy/sale series with the same unlabelled era break, and it is also not a monthly source; see ranking in section 14.

## 9. Rebasing and redenomination findings

Official evidence for the ÷1,000 factor and its effective date (all anonymous public PDFs, base `https://www.boz.zm`):

| Publication | File path | PDF page (printed) | Section / table | Exact wording |
|---|---|---|---|---|
| 2012 Annual Report | `/sites/default/files/migrated/annual-reports/2012/BOZANNUALREPORT2012.pdf` | 72 | 5.2.2 *Currency Rebasing* | "This involved dividing each unit of the Kwacha by 1,000." and "transition period of six months, from January 1, 2013 to June 30, 2013 during which both old and rebased versions of the Kwacha would be in circulation. The rebased currency was officially launched on 31st December, 2012" |
| 2013 Annual Report | `/sites/default/files/migrated/annual-reports/2013/BOZANNUALREPORT2013.pdf` | 68 | 5.2.1 *Currency Rebasing* | "The Bank implemented the currency rebasing on 1st January 2013 and issued the new family of the Zambian currency into circulation." |
| 2013 Annual Report | same | 91 (83) | financial-statement basis of preparation note | "With effect from 1 January 2013, the Zambian Kwacha was rebased by dividing the currency by 1,000. As part of the process, the currency code was changed to ZMW from ZMK although the currency symbol “K” remains unchanged. These financial statements have been prepared in the rebased currency. Accordingly, the comparatives have been restated by dividing them by 1,000." |
| 2013 Annual Report | same | 70 (62) | footnote under Chart 47 (cheques cleared, 2011 to 2013) | "Figures for 2012 were rebased consistent with currency rebasing undertaken in 2013" |

Effective-date evidence: the 2012 report states the rebased currency was launched on 31 December 2012 with a transition from 1 January to 30 June 2013; the 2013 report states the rebasing was implemented on 1 January 2013 and the code changed from ZMK to ZMW with effect from that date. The two reports state the factor consistently (1,000).

Findings:

- **Statistical tables versus financial-statement wording.** The restatement wording ("comparatives have been restated", "Figures for 2012 were rebased") applies to the financial statements and to Chart 47 respectively. It is not a statement about Table 13 and must not be applied to statistical tables automatically. The ÷1,000 factor itself is stated generally for the currency, so a conversion rule for a particular table has to be justified per publication and table, not by that wording alone.
- **2012 Annual Report, Table 13 (PDF p. 145):** contains historical old-kwacha statistical values only (December 1995 to 2009, monthly 2010-01 to 2012-12, for example Dec 2012 mid `5,208.46`). The table carries no unit label and no rebasing note, but every row is in the old regime (the report was prepared before the rebased currency took effect).
- **2013 Annual Report, Table 13 (PDF p. 146):** contains historical old-unit rows (December 2000 to 2010, monthly 2011 and 2012; the 2011 to 2012 rows are identical to the 2012 report) alongside 2013 rows in the rebased regime (Jan `5.28`), in one column with no unit label, no ZMK/ZMW marker and no table footnote. The regime change sits between 2012-12 (`5,208.46`) and 2013-01 (`5.28`).
- The regime of each row is therefore established by the publication and the effective date (rows for periods before 1 January 2013 in these tables are old kwacha; rows from 2013-01 are rebased), not by magnitude. Magnitude was used only as a consistency check: the 2013 report's rebased 2012 annual average in the narrative (`K5.1415/US$`) is within 0.1% of the mean of Table 13's 2012 monthly mids divided by 1,000 (5.1368). That check is corroboration, not the grounding for the rule.
- Fortnightly issues (2014 on) are entirely in the rebased regime; no ZMK rows appear on the pages inspected.
- Without conversion the Dec 2012 to Jan 2013 change would read about -99.9%. Any use of pre-2013 rows needs an explicit, recorded, per-publication ÷1,000 rule and stored original value, regime, rule and normalized value (Gate 4).

## 10. Monthly-average definition

**Methodology not established.** No BoZ publication inspected defines "Monthly Average" (the fortnightly notes cover REER base, revision dates and lags only). Observations that are not definitions:

- The 2026 daily average rate equals the mean of the day's three intraday observations (section 5).
- The mean of the daily workbook mid-rates `(buy + sale) / 2` equals the published monthly mid within printed rounding (about 0.005) in most months checked (median absolute difference about 0.0025 over 201 months; outliers 2021-08, 2021-12, 2024-03, 2021-05 where the workbook is missing days or differs, unresolved).
- 2013 annual averages differ slightly from the mean of monthly mids (5.3914 vs 5.3925), so annual figures are not a simple mean of the published months.

## 11. Precision

| Source | Precision |
|---|---|
| Fortnightly Table 02 / 01A | 2 decimals, all periods |
| 2012 / 2013 Annual Reports Table 13 | 2 decimals of ZMK (old units) and 2 decimals of ZMW (2013) |
| 2025 Annual Report Table 10 | 1 decimal |
| Average-rates JSON node | 4 decimals (strings) |
| Average-rates and daily workbooks | Excel floats (15 to 17 digits); not source precision |

Store the precision as printed; never extend 2-decimal values. The 1-decimal and 2-decimal publications agree within printing resolution in all 102 recomputed comparisons (section 7). A fixed absolute rounding difference implies a percentage difference that depends on the exchange-rate level (for example 0.005 is about 0.08% of a level near 6 and about 0.02% of a level near 24).

## 12. Revision behaviour

Across the 13 text-bearing fortnightly issues sampled (13 of 254 listed), 126 of the 153 distinct months appear in more than one issue (up to 6 issues per month; lags up to about 12 months; 2023: 12 months over 24 comparisons, 2024: 12 months over 71, 2025: 12 months over 53). Re-verified in this pass: **0 differing buying/selling/mid triples** among the 126 repeated months. The 2012 and 2013 annual reports agree on all 35 overlapping rows (2011-01 to 2012-12 and the 11 December rows: 0 differences).

Conclusion, and only this: **No revisions were observed in the sampled overlaps.** Only a subset of all fortnightly issues was sampled, so this is not evidence that BoZ does not revise Table 02, and the importer must not assume revisions cannot occur. The publication itself carries a note (under the real-effective-exchange-rate table) that revisions were made to 2016 figures in March 2017 and to figures for 18 to 29 December 2023 in January 2024; its scope is not stated. Storage must be conflict-aware: identical repeats are compatible provenance, differing repeats are surfaced as a revision/conflict (Gate 6).

## 13. Comparison with stored 2026 intraday data

Project method (replicated read-only against local `fx_rates`): Africa/Lusaka dates, mean of intraday mids per day, equal mean across observed days, compared with the BoZ official monthly mid (fortnightly issue 21, identical in issues 8 and 14 where present):

Signed difference = project average minus official mid. Percentage difference = `100 x (project - official) / official`. Absolute difference is the magnitude of the signed difference.

| Month | Project days / obs | Project avg (mid) | BoZ official mid | Signed difference | Absolute difference | Percentage difference |
|---|---|---|---|---|---|---|
| 2026-01 | 5 / 13 | 19.8050 | 20.09 | -0.2850 | 0.2850 | -1.419% |
| 2026-02 | 1 / 2 | 18.2903 | 18.89 | -0.5997 | 0.5997 | -3.175% |
| 2026-03 | 8 / 21 | 19.1402 | 19.26 | -0.1198 | 0.1198 | -0.622% |
| 2026-04 | 19 / 56 | 19.0982 | 19.10 | -0.0018 | 0.0018 | -0.009% |
| 2026-05 | 19 / 57 | 18.8346 | 18.83 | +0.0046 | 0.0046 | +0.024% |
| 2026-06 | 22 / 66 | 17.8151 | 17.82 | -0.0049 | 0.0049 | -0.028% |
| 2026-07 | 21 / 63 | 18.3552 | 18.36 | -0.0048 | 0.0048 | -0.026% |
| 2026-08 | 19 / 57 | 18.8957 | 18.90 | -0.0043 | 0.0043 | -0.023% |
| 2026-09 | 21 / 63 | 19.4166 | 19.42 | -0.0034 | 0.0034 | -0.017% |

October and November 2026 have no official monthly value yet in the sampled issues (the only later issue listed, number 22, has no Table 02). Reading: in the six **well-covered sampled months** (at least 15 stored days; this is a descriptive threshold, not a completeness claim) the absolute difference is below 0.005, the printing resolution of the 2-decimal official value, i.e. within 0.03%. In the three sparsely stored months (5, 1 and 8 stored days) the difference is 0.6% to 3.2%. Sparse stored coverage is associated with larger observed differences in this sample, but the source methodologies are not established as identical, so the cause is not attributed. Official monthly values remain the preferable long-horizon input because they do not depend on how many days the project stored.

## 14. Source ranking

| Rank | Source | Category | Verdict |
|---|---|---|---|
| 1 | Official structured monthly API/download | D | **none found** |
| 2 | Official structured workbook explicitly designed as monthly averages | D | **none found** (`AVERAGE_FXRATES_*.xlsx` is daily buy/sale, category B, no mid, unlabelled ZMK/ZMW break, six rows inconsistent with the column structure, floats) |
| 3 | Fortnightly Statistics Table 02 / 01A | D, plus C weekly and daily blocks | **best available**: monthly mid/buy/sell, 2014-01 to 2026-09, 2 decimals, no revisions observed in sampled overlaps, PDF only |
| 4 | Annual Reports Table 13 (2012, 2013) | D (2010-01 to 2013-12), Dec-only earlier | usable only with an explicit ÷1,000 policy for 2010 to 2012 |
| 4 | Annual Report 2025 Table 10; booklets | D (December and 2024 to 2025 only, 1 dp) / E | not a continuous monthly source; cross-check only |
| 5 | Analyst workbook (`DAILY_RATES_*.xlsx`) | A | keep rejected |

Source categories used (not interchangeable): A intraday observations; B daily averages; C weekly averages; D monthly averages; E end-period; F annual averages.

## 15. Architecture recommendation (not implemented)

Keep `fx_rates` and the live adapter exactly as they are. If implemented, add a **separate** dataset, for example `fx_monthly_averages`, never reusing intraday `observed_at`:

- Identity: `base_currency`, `quote_currency` (`USD`/`ZMW`), `observation_month` (first day of month), `price_basis = monthly_average`, `source_provider` (BoZ), `source_series`/table (`fortnightly_table_02`).
- Published values as printed, never recomputed: `published_buy`, `published_sell`, `published_mid` (each a decimal string kept at printed precision), `published_precision`.
- Normalized values (only where a documented conversion applies; equal to the published values in the rebased regime): `normalized_buy`, `normalized_sell`, `normalized_mid`, plus `original_currency_regime` (`ZMW` or `ZMK`), `conversion_rule` (null or an identifier of the documented rule) and the evidence reference for it.
- Provenance: `source_publication_title` or filename, `source_url`, `source_pdf_page`, `source_table_id` (`Table 02`), `retrieved_at`/`imported_at`.
- `anomaly_exception_id` (null unless a named exception applies, section 19 Gate 5) and `rights_status`.
- The published mid is stored directly. It is not derived from buy and sell.
- Unique key `(pair, observation_month, source_series)`; differing re-import values abort (conflict-aware), matching the backfill behaviour. Because overlapping issues repeat the same months, record the first and latest issue that confirmed a value as compatible provenance, and surface a differing repeat as a revision/conflict for explicit handling rather than overwriting it.
- Importer: manual job like `backfill_usdzmw`, pinned to Table 02, fail loudly on layout or arithmetic anomalies (`buy <= mid <= sell` and `mid` within rounding of `(buy + sell) / 2`), with an explicit, named exception list for published anomalies.
- Phase 2 (optional, gated): 2013 months from the 2013 Annual Report (already rebased) and 2010 to 2012 months from the 2012 Annual Report (old units, with a stored ÷1,000 policy and per-row `source_unit`).

## 16. Copper analytical impact (research-only)

IMF Copper in the local store runs 1992-01 to 2026-10 (418 months). Only the FX range limits the matched sample.

Terms: **level months** are months with a usable FX level matched to a Copper level; a **transition pair** is two consecutive usable level months; the **change endpoint** is the later month of a pair.

| Option | FX months | Matched level months | Usable level months | Transition pairs (eligible changes) | Change endpoints | 12M rolling points |
|---|---|---|---|---|---|---|
| Today (observed-days, replicated) | 2026-01 to 2026-11 | 10 (2026-01 to 2026-10) | 9 (2026-02 to 2026-10; January is partial and excluded) | **8** (Feb to Mar, ..., Sep to Oct; Jan to Feb is excluded) | **March to October 2026** | 0 |
| Fortnightly only | 2014-01 to 2026-09 | 153 | 153 | **152** | 2014-02 to 2026-09 | 141 |
| + 2013 Annual Report | 2013-01 to 2026-09 | 165 | 165 | 164 | 2013-02 to 2026-09 | 153 |
| + 2010 to 2012 Annual Report (÷1,000) | 2010-01 to 2026-09 | 201 | 201 | 200 | 2010-02 to 2026-09 | 189 |

Transition pairs are not computed as "matched months minus one" in general. For the official-series rows no month was excluded (the published months are consecutive with no gap, and all values are positive), so pairs equal usable level months minus one there. For the "today" row, one level month (January, a partial-source month) is excluded and its Jan to Feb pair with it, so 10 matched months give 8 pairs, not 9. The "today" row replicates the production eligibility rules (consecutive months, partial-source month excluded, positivity); it is not a production API call.

**Arithmetic re-verified in this pass:** 153, 165 and 201 matched levels; 152, 164 and 200 changes (levels minus one, no month excluded); 141, 153 and 189 twelve-change windows (changes minus 11, since the 12-change windows slide by one change). Endpoints are the month after the first level through the last level.

**Research-only analytics preview** (not for the UI or production API). Status: re-run in this pass with `pearson` and `sensitivity_beta` from `apps/api/app/services/copper_kwacha.py` over the inputs below, reproducing the values shown. The inputs were assembled in a session scratch area and are **not committed**, so the coefficients are reconstructable (from the IMF Copper rows in the local store and the cited publications) but not reproducible from this repository alone; treat them as indicative.

- Copper: IMF monthly copper series from the local store (418 months, 1992-01 to 2026-10); change = `level_t / level_(t-1) - 1`.
- FX: the published BoZ mid-rate (not a derived mid), at its printed 2-decimal precision, for each month in the sample: fortnightly Table 02 (153 months); 2013 rows from the 2013 Annual Report Table 13 (rebased regime); 2010 to 2012 rows from the 2012 Annual Report Table 13 divided by 1,000 under a **research-only** conversion rule (the production rule would need Gate 4 per-publication grounding); change defined the same way. Published anomaly months (2012-04, 2013-12, 2019-03, 2022-11) were used as published.
- Pairs are consecutive-month pairs with positive levels. The last row uses the project's observed-days monthly averages (stored intraday rows) rather than the published mid.

| Sample | Changes | Pearson r | Beta (Sxy/Sxx) |
|---|---|---|---|
| 2014-01 to 2026-09 | 152 | -0.040 | -0.043 |
| 2013-01 to 2026-09 | 164 | -0.041 | -0.044 |
| 2010-01 to 2026-09 | 200 | -0.076 | -0.073 |
| Project observed-days (levels 2026-02 to 2026-10; change endpoints 2026-03 to 2026-10) | 8 | -0.255 | -0.287 |

Reading: the longer-history estimate differs materially from the current eight-change production estimate. The current estimate has limited empirical support because it uses only eight eligible monthly changes. No confidence interval or formal uncertainty analysis was performed. The official inputs are 2-decimal values, so the research figures carry input rounding whose relative size depends on the exchange-rate level. The figures are descriptive, not significance or causal claims. They must be recomputed inside the application before any production use (see the open items at the end of section 19).

## 17. Rights and access

- All sources above were anonymous, public and non-authenticated; no bypass used.
- No licence, copyright or reuse statement was found in the publications inspected, nor on the site bundle beyond a privacy modal. Redistribution and commercial-use status for BoZ data remain **`review_required`**. Public access is not treated as permission to redistribute. BoZ was not contacted and no email was sent.
- Access characteristics: JSON:API list and file URLs were consistent in structure across the sampled listings but file names change on republication; PDFs live under `/sites/default/files/migrated/fortnight-statistics/<year>/` (older issues) and `/sites/default/files/<yyyy-mm>/` (newer); there is no documented rate limit; scraping a web UI is not required.

## 18. Final verdict

**APPROVED FOR LOCAL MONTHLY IMPLEMENTATION — RIGHTS REVIEW REQUIRED** (applies to the proposed monthly FX dataset only, not the existing intraday adapter).

- No structured BoZ monthly-average source was found during this research. The identified JSON/API data and the historical workbook are daily; neither is a structured monthly-average source. The defensible source found is the **BoZ Fortnightly Statistics Table 02 / 01A monthly averages: 153 months, 2014-01 to 2026-09, 2 decimals, no revisions observed in the sampled overlaps**. The reconstructed series contains every calendar month in that range, which does not mean every fortnightly issue was parsed. Annual-report tables extend the series to 2010-01 only with an explicit, publication-specific rebasing rule.
- Design choice for Copper (not made here): **Option B** (official monthly series over the whole available range, as a new methodology version) is preferable to **Option A** (keep the short observed-days sample) once implemented; **Option C** (splicing official history with project-derived recent months) is **not recommended** because definitions are not established to be identical. In the well-covered 2026 months the two agree within 0.03%, but this is observation, not proof.
- The current observed-days method is not wrong: it is an explicitly documented derived method over the stored intraday rows. The question is only whether an official series is preferable for long-horizon monthly analytics.
- **Storage:** a separate future model such as `fx_monthly_averages`. Monthly observations are never written to the intraday `fx_rates`; they have different observation semantics.
- **Copper versioning:** `copper_zm_v1` stays unchanged and reproducible. If official monthly BoZ FX becomes the Copper input, introduce a new methodology such as `copper_zm_v2`, and use new correlation and beta methodology identifiers (leaving `copper_kwacha_*_v1` unchanged) because the underlying FX population changes.

The verdict is retained because the corrected evidence (section 6.1 to 6.2, 7, 9, 12) supports a local, separate monthly series from Table 02 **provided all nine gates in section 19 are satisfied**.

It means: local implementation of a separate official monthly FX series may proceed; the preferred source is BoZ Fortnightly Statistics Table 02; the implementation must satisfy the discovery, table-identification, column/year, rebasing, anomaly, provenance and fixture gates; redistribution and commercial-use rights remain unresolved.

It does **not** mean: public redistribution is approved; commercial use is approved; a parser already exists; or monthly data belongs in `fx_rates`.

## 19. Implementation gates

Approval in section 18 is conditional: implementation may start only when **every one of Gates 1 to 9** below is explicitly satisfied and recorded.

**Gate 1: deterministic publication discovery.** The implementation must define how production identifies the authoritative publication set: which JSON:API content type and series filter enumerate the Fortnightly Statistics issues, how issues are ordered, how a superseded or re-uploaded file for the same issue is resolved, and which issue is authoritative when months overlap (section 12). No broad web scraping, no crawling of site pages, and no constructed file URLs (names are irregular; section 6). An unexpected listing shape, an empty listing or an unexplained disappearance of previously listed issues must fail the run rather than fall back to guessing.

**Gate 2: exact table identification.** The parser must positively identify, for each file: the correct publication family (Fortnightly Statistics), the correct page and table block, `Table 02`, the heading `KWACHA/US DOLLAR EXCHANGE RATES` and the `Monthly Averages` block. It must reject ambiguous or missing matches: zero matches, more than one candidate block, a matching heading with a different column layout, rows for a month outside the block, or an issue with no table (as in 2026 issue 22). It must not fall back to a looser text search, to Table 01A, or to a different publication family when Table 02 cannot be identified.

**Gate 3: explicit columns and year/month handling.** The parser must strictly identify and map month, year, BoZ buying, BoZ selling and the published BoZ mid-rate. It must not infer a year from the file's download date or listing year (the 2016 and 2017 listed issues carry 2014 and 2016 months, section 6.1); it must stop if the publication has no unambiguous year context; it must not silently shift columns when a layout changes; it must not accept a row merely because it has three numeric values; it must reject ambiguous, missing or duplicated month entries; and it must preserve the exact published month/year association.

**Gate 4: redenomination and rebasing.** Any conversion of pre-2013 values must rest on explicit official publication evidence (section 9: ÷1,000, effective 1 January 2013, launch 31 December 2012). No magnitude heuristic such as `value > 1000`. Publication and table eras are treated explicitly: old-kwacha published values are distinguished from rebased values, and financial-statement restatement wording is not applied to statistical tables automatically. Where a value is converted for a normalized ZMW series, store the original published value, the original unit/regime, the conversion rule, the normalized value and the source publication and page. Fail closed if the regime cannot be determined from documented rules.

**Gate 5: named anomaly exceptions.** Default parsing and validation stay strict. Each known source-published exception is a narrow, explicit entry with: month; affected field or validation rule; exact published buy/sell/mid; publication title or filename; source URL; PDF page and table; and the reason it is accepted. Known cases: 2012-04 (selling below buying), 2013-12 (mid inconsistent with buy/sell), 2022-11 (mid inconsistent with buy/sell), and, from the research arithmetic check, 2019-03 (section 6.2; to be confirmed by the implementation). The exception means "accepted because this is the value BoZ actually published", not "the value has been mathematically corrected". Do not recompute the mid, swap buying and selling, silently repair a value, or disable validation globally.

**Gate 6: provenance, revision and conflict handling.** Every monthly row retains: pair; observation month; published buying, selling and mid; normalized values if converted; original currency/unit regime; published precision; publication title or filename; source URL; PDF page; table identifier; retrieved/imported timestamp; and provider identifier. A later publication repeating identical values is compatible provenance (idempotent). A later publication with different values is never silently overwritten; it is surfaced as a revision/conflict for explicit handling. The research finding is only that no revisions were observed in the sampled overlaps; no assumption that revisions cannot occur is encoded.

**Gate 7: preserve the published mid.** Table 02 publishes a mid-rate; the implementation stores it directly. It must not compute `(buy + sell) / 2` and must not replace an inconsistent published mid with a derived one. A derived mid would be a different methodology and is not needed for this source.

**Gate 8: parser fixtures across publication eras.** The parser needs small committed fixtures covering at least: an early fortnightly publication, a middle-period publication, a recent publication, an issue with repeated historical months, an issue without Table 02, a malformed or shifted table, a known published anomaly, and, if the annual-report extension is implemented, an annual-report rebasing-era table. Fixtures must test: Table 02 identification, column mapping, year/month handling, exact decimals, rejection of ambiguous layout, anomaly exceptions, and duplicate/revision behaviour. A parser validated against a single PDF layout is not approved (the heading and column layout differ between 2016, 2017 to 2023 and 2024 to 2026 issues, section 6.1).

**Gate 9: completeness and a deterministic import boundary.** A production import defines exactly which publication range is ingested: deterministic discovery, deterministic chronological ordering, an explicit earliest and latest requested publication. Before committing any data it reports missing publications, missing Table 02 blocks, duplicate months, conflicts, and the final coverage and gaps. It does not infer missing months, interpolate or forward-fill, and it does not treat the reconstructed 2014-01 to 2026-09 coverage as proof that every fortnightly issue exists or was parsed.

Operational items that remain open and are not gated above: the rights review (BoZ not contacted; local versus wider use), the product decision on PDF dependence, the phase scope (2014-01 onward only, or the 2013 and 2010 to 2012 annual-report extension under Gate 4), a separate table/migration with its own methodology id, a refresh policy that excludes the partial latest month, and recomputing the research coefficients inside the application before any production use.

Status: deeper FX history is **research in progress**; nothing here is implemented.
