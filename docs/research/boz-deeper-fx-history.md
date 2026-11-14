# BoZ deeper USD/ZMW history: source discovery

Status: **research only**. Nothing in this document is implemented. No provider, migration, schema, route or Copper methodology change was made, and Bank of Zambia (BoZ) was not contacted. Research dates: retrieved 2026-11-14/15 from public, anonymous BoZ endpoints; every candidate file was downloaded once with a descriptive `User-Agent`, requests were spaced at least 2 s apart and the whole investigation was about 40 requests.

## 1. Objective

Find out whether ZM Terminal can defensibly extend official USD/ZMW history earlier than the intraday JSON boundary of `2026-01-22`, with the **monthly average** series as the main target (Copper and other monthly analytics), without inventing timestamps, converting monthly data into daily rows, mixing definitions, mishandling the 2013 rebasing, adding undocumented calculations, or scraping unstable sources.

## 2. Current limitation

- The live adapter and `fx_rates` hold timestamped intraday observations (about 09:30 / 12:30 / 15:30 Africa/Lusaka) from `boz_zmw_usd_daily_exchange_rates`, which serves nothing before 2026-01-22. Stored: 492 rows, 2026-01-22 to 2026-11-13.
- Copper analytics (`COPPER ZM`, `copper_kwacha_corr_v1`, sensitivity) use the project's observed-days monthly FX average, so the matched sample is limited to 2026 (production-method sample replicated in section 16: 9 usable months, 8 eligible changes).
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
- **Six corrupted rows** (2006-01-04 to 2006-01-11): the Dollar columns hold `43649`, `0.6458...` and the text `Stable` / `Stay Out`, i.e. cells overwritten by unrelated content. A parser has to detect and skip them, which is inference.
- Values are Excel binary floats (for example `5.2062962962962951`), with 15 to 17 significant digits that are not source precision. The 4-decimal JSON strings agree with the workbook row for the same day (2026-11-12: `20.3998` vs `20.39980238...`).
- **Currency eras are unlabelled in the file.** The same column holds ZMK-scale values until 2012-12-28 (`5136.61`) and rebased values from 2013-01-02 (`5.206...`), with no unit header, note or era marker.
- Extra observation (not a documented definition): for 2026-11-12 the three stored intraday buys (20.3931, 20.4000, 20.4063) average to 20.3998, exactly the published daily "average" buy. The daily average therefore looks like the mean of that day's intraday observations. This is numerical agreement only. **Methodology not established.**

Request discipline: anonymous GET only, no authentication or bypass, JSON:API and files are static-like and cacheable. A future importer would need one list request and one file request per run; no rate problem expected, but BoZ has published no API terms.

## 6. Fortnightly statistical publications

Listing: `GET /jsonapi/node/statistics?filter[field_series.id]=<Fortnightly Statistics term>&include=field_statistic_files` (six pages of 50) returns 254 PDFs: 1 in 2016, 29 in 2017, then about 26 a year through 2026 (22 in 2026). 14 were downloaded: 2016 `Vol24No01`, 2017 `Vol25No6`, 2019, 2020, 2021, 2022, 2023, 2024 (issue 26 of each), 2025 issues 14 and 26, 2026 issues 8, 14, 21 and 22.

- Table 02 `KWACHA/US DOLLAR EXCHANGE RATES` (also summarised in Table 01A `BANK OF ZAMBIA MID-RATES`, with `End of Period` and `Period Average` K/USD) gives **Bank of Zambia Rates buying / selling / mid-rate** and Bureau Rates, under "Monthly Averages", then weekly averages and recent daily values. Text layer present; layout regular.
- Each issue carries a rolling window of 12 to 36 monthly rows.
- **Earliest month**: 2014-01, in the earliest listed file (`StatisticsFortnightly2016Vol24No01.pdf`, whose contents run to December 2016, so its label does not match its contents). **Latest**: 2026-09 (issue 21, 2026-10-27); the one-page issue 22 carries no table.
- Union of the 13 text-bearing issues: **153 distinct months, 2014-01 to 2026-09, no gap**.
- **Precision: 2 decimals** in every period (Table 02 and 01A).
- Same-definition check: Table 02 monthly mid equals Table 01A `Period Average`.
- Source anomaly: **2022-11 prints buying 16.16, selling 16.57, mid 16.55**, whereas `(buy + sell) / 2` is 16.365 and the usual buy/sell spread is 0.05; it is repeated unchanged in later issues. A validator must flag it rather than silently accept or repair it.
- A note printed under the neighbouring real-effective-exchange-rate table in the 2025 issue says revisions were made to 2016 figures in March 2017 and to figures from 18 to 29 December 2023 on 19 January 2024; its scope is not stated, and no monthly change was observed (section 12).
- The `Volume` label scheme and file names are irregular (`Vol25No6`, `IssueNo_26`, `IssueNo_13_1`, `_0` suffixes), so discovery must go through the JSON:API list, not constructed URLs.

PDF parsing is **not** implemented; this is a viability finding only. Viability: viable, with a fail-loud parser pinned to Table 02, because the layout was the same across 2016 to 2026 in all 13 issues and a simple regex recovered all 153 months; the risks are layout changes and the published anomalies (section 18).

## 7. Annual reports and statistical booklets

Annual Reports (the listing has 35 publication nodes, which include the National Payment Systems annual reports; BoZ Annual Reports run 2004 to 2025 with no 2006 report):

| Report | Table | Content | Precision |
|---|---|---|---|
| 2012 | Table 13 `KWACHA/US DOLLAR EXCHANGE RATES`, "Monthly Average" | **December rows 1995 to 2009, then every month 2010-01 to 2012-12**, buy/sell/mid for BoZ and Bureau, in **old (ZMK-scale) kwacha** (for example Dec 2012 mid `5,208.46`) | 2 decimals of ZMK |
| 2013 | Table 13, "Monthly Average" | December rows 2000 to 2010, monthly 2011 to 2012 (old units, identical to the 2012 report: 35 overlapping rows, 0 differences), monthly **2013 in rebased units** (Jan `5.28`) | 2 decimals |
| 2025 | Table 10 `Kwacha/US Dollar Exchange Rates, (Monthly Average) Dec. 2014 - Dec. 2025` | December rows 2014 to 2023, then every month 2024 and 2025 | **1 decimal** |

- The 2025 report is **not** a continuous monthly history: before 2024 it holds one December value per year. Its 1-decimal values equal the 2-decimal fortnightly values rounded, except 6 of 93 comparisons that fall on exact `.x5` ties (for example Dec 2014 mid 6.35 prints 6.3, Aug 2025 sell 23.25 prints 23.2), i.e. printing rounding of unpublished finer values.
- "December" rows in these tables are December **monthly averages**, not end-of-period rates (Dec 2014 mid 6.34 in the fortnightly table, 6.3 in the 2025 report; the end-of-period rate is a different column in Table 01A).
- Booklets (`FINANCIAL AND OTHER STATISTICS`, 2014 to 2025): the 2025 booklet (52 pages) has `Table 16/17 ... End Period, 2016 - 2025` only, i.e. annual end-period values (category E); the 2014 booklet is a 36-page PDF with no text layer (scanned images; not machine-readable without OCR); not inspected further.
- Data-quality prints in the annual tables: April 2012 selling `5,250.97` below buying `5,283.60` (identical in both reports); December 2013 mid `5.54` above selling `5.53`.
- The user brief expected 4-decimal annual-report tables; the 2025 Annual Report table is 1 decimal. Four-decimal annual figures exist only in narrative text (for example `K5.3914/US$`, the 2013 annual average).

## 8. Reassessment of the previously rejected XLSX

The file the project rejected is **not** the average-rates workbook. It is `DAILY_RATES_*.xlsx`, published through `/jsonapi/node/historical_daily_zmw_usd` (the Daily USD/ZMW time-series page), 2.4 MB, with:

- three sheets: hidden `Table1` (Power Query table plus connection), `Interbank Average Rates` (18,739 rows; intraday date/time/buy/mid/sell from **2011-09-22** to 2026-11-13) and `Retail Average Rates` (from 2025-09-15);
- a pivot table, a chart, a cached pivot of 8,553 rows and an external-data connection;
- one `#VALUE!` cell (a `Volatility` column that is also analyst-added), whitespace-padded date cells and **dates only on the first row of each day** (the following rows are blank, so a date has to be forward-filled);
- Excel-float values and ZMK-scale values (`4939.0625`) before rebasing next to rebased ones, again with no unit label.

Verdict: **keep rejected**. It is the intraday analyst workbook, not a monthly series, and safe extraction would require inference (date fill-down, time-zone assumption, era scaling, skipping analyst columns). The separate average-rates workbook is cleaner (one sheet, explicit dates, no errors) but is a daily buy/sale series with the same unlabelled era break, and it is also not a monthly source; see ranking in section 14.

## 9. Rebasing and redenomination findings

Exact source wording:

- 2012 Annual Report, section 5.2.2 *Currency Rebasing*: "This involved dividing each unit of the Kwacha by 1,000." and "transition period of six months, from January 1, 2013 to June 30, 2013 during which both old and rebased versions of the Kwacha would be in circulation. The rebased currency was officially launched on 31st December, 2012".
- 2013 Annual Report, financial-statement note: "With effect from 1 January 2013, the Zambian Kwacha was rebased by dividing the currency by 1,000. As part of the process, the currency code was changed to ZMW from ZMK although the currency symbol “K” remains unchanged. ... the comparatives have been restated by dividing them by 1,000." Section 5.2.1: "The Bank implemented the currency rebasing on 1st January 2013".
- 2013 Annual Report, a table footnote: "Figures for 2012 were rebased consistent with currency rebasing undertaken in 2013".

Findings:

- **BoZ's own Table 13 mixes eras with no label.** In both reports the 2010 to 2012 rows are in old units (4,500 to 5,300) and the 2013 rows in rebased units (about 5.3), in one column, with no unit, no ZMK/ZMW label and no footnote on that table. The break sits between 2012-12 (`5,208.46`) and 2013-01 (`5.28`).
- The values around 5 in 2011 to 2012 in the narrative and in later documents are rebased equivalents; the tables themselves are not restated. This was **not** inferred from magnitude alone: the ÷1,000 factor is stated by BoZ (above), and the 2013 report's own rebased 2012 annual average (`K5.1415/US$`) is within 0.1% of the mean of the table's 2012 monthly mids divided by 1,000 (5.1368).
- Fortnightly issues (2014 on) are entirely rebased kwacha; no ZMK rows appear.
- Without conversion the Dec 2012 to Jan 2013 change would read about -99.9%. Any use of pre-2013 rows needs an explicit, recorded ÷1,000 policy and a stored `currency_era` / `source_unit` note.

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

Store the precision as printed; never extend 2-decimal values. Where two BoZ publications differ only in printing (1 vs 2 decimals) it is rounding, documented above.

## 12. Revision behaviour

Across the 13 text-bearing fortnightly issues, 126 of the 153 distinct months appear in more than one issue (up to 6 issues per month; lags up to about 12 months; 2023: 12 months over 24 comparisons, 2024: 12 months over 71, 2025: 12 months over 53). **0 differences** in buying, selling or mid. The 2012 and 2013 annual reports agree on all 35 overlapping rows. Classification: **stable**; no revision observed. Caveats: sampling covered 14 of 254 issues, and a note in the publication reports revisions to 2016 figures (March 2017) and to 18 to 29 December 2023 figures (January 2024) without stating which table they affect. Storage should still be conflict-aware (reject a differing value for an existing month, as the backfill job does for `fx_rates`).

## 13. Comparison with stored 2026 intraday data

Project method (replicated read-only against local `fx_rates`): Africa/Lusaka dates, mean of intraday mids per day, equal mean across observed days, compared with the BoZ official monthly mid (fortnightly issue 21, identical in issues 8 and 14 where present):

| Month | Project days / obs | Project avg (mid) | BoZ official mid | Abs diff | % diff |
|---|---|---|---|---|---|
| 2026-01 | 5 / 13 | 19.8050 | 20.09 | -0.2850 | -1.419% |
| 2026-02 | 1 / 2 | 18.2903 | 18.89 | -0.5997 | -3.175% |
| 2026-03 | 8 / 21 | 19.1402 | 19.26 | -0.1198 | -0.622% |
| 2026-04 | 19 / 56 | 19.0982 | 19.10 | -0.0018 | -0.009% |
| 2026-05 | 19 / 57 | 18.8346 | 18.83 | +0.0046 | +0.024% |
| 2026-06 | 22 / 66 | 17.8151 | 17.82 | -0.0049 | -0.028% |
| 2026-07 | 21 / 63 | 18.3552 | 18.36 | -0.0048 | -0.026% |
| 2026-08 | 19 / 57 | 18.8957 | 18.90 | -0.0043 | -0.023% |
| 2026-09 | 21 / 63 | 19.4166 | 19.42 | -0.0034 | -0.017% |

October and November 2026 have no official monthly value yet in the sampled issues (October appears in a later issue). Reading: the six months with at least 15 stored days agree within 0.03% (below the 0.005 printed rounding of the official value); the three poorly covered months (5, 1 and 8 stored days) differ by 0.6% to 3.2% because the project averages only the days it stores. This indicates the project's method is compatible with the official figure **where coverage is complete**, but it does not prove they are the same method; official monthly values are the better long-horizon input because they need no stored-day coverage.

## 14. Source ranking

| Rank | Source | Category | Verdict |
|---|---|---|---|
| 1 | Official structured monthly API/download | D | **none found** |
| 2 | Official structured workbook explicitly designed as monthly averages | D | **none found** (`AVERAGE_FXRATES_*.xlsx` is daily buy/sale, category B, no mid, unlabelled ZMK/ZMW break, corrupt rows, floats) |
| 3 | Fortnightly Statistics Table 02 / 01A | D, plus C weekly and daily blocks | **best available**: monthly mid/buy/sell, 2014-01 to 2026-09, 2 decimals, stable, PDF only |
| 4 | Annual Reports Table 13 (2012, 2013) | D (2010-01 to 2013-12), Dec-only earlier | usable only with an explicit ÷1,000 policy for 2010 to 2012 |
| 4 | Annual Report 2025 Table 10; booklets | D (December and 2024 to 2025 only, 1 dp) / E | not a continuous monthly source; cross-check only |
| 5 | Analyst workbook (`DAILY_RATES_*.xlsx`) | A | keep rejected |

Source categories used (not interchangeable): A intraday observations; B daily averages; C weekly averages; D monthly averages; E end-period; F annual averages.

## 15. Architecture recommendation (not implemented)

Keep `fx_rates` and the live adapter exactly as they are. If implemented, add a **separate** dataset, for example `fx_monthly_averages`, never reusing intraday `observed_at`:

- `base_currency`, `quote_currency` (`USD`/`ZMW`), `observation_month` (first day of month), `buy`, `mid`, `sell` (nullable decimals as printed), `price_basis = monthly_average`, `source_series`/table (`fortnightly_table_02`), `source_publication` (volume/issue), `source_url`, `source_unit` and `currency_era` (`ZMW` only for the first phase), `source_precision`, `retrieved_at`, `published_value_note` (for printed anomalies such as 2022-11), `rights_status`.
- Unique key `(pair, observation_month, source_series)`; differing re-import values abort (conflict-aware), matching the backfill behaviour. Because overlapping issues repeat the same months, record the first and latest issue that confirmed a value rather than creating revisions until one actually occurs.
- Importer: manual job like `backfill_usdzmw`, pinned to Table 02, fail loudly on layout or arithmetic anomalies (`buy <= mid <= sell` and `mid` within rounding of `(buy + sell) / 2`), with an explicit, named exception list for published anomalies.
- Phase 2 (optional, gated): 2013 months from the 2013 Annual Report (already rebased) and 2010 to 2012 months from the 2012 Annual Report (old units, with a stored ÷1,000 policy and per-row `source_unit`).

## 16. Copper analytical impact (research-only)

IMF Copper in the local store runs 1992-01 to 2026-10 (418 months). Only the FX range limits the matched sample.

| Option | FX months | Matched months | Eligible changes | 12M rolling points |
|---|---|---|---|---|
| Today (observed-days, replicated) | 2026-01 to 2026-11 | 10 (2026-01 to 2026-10; 2026-01 partial) | **8** (2026-02 to 2026-10) | 0 |
| Fortnightly only | 2014-01 to 2026-09 | **153** | **152** | 141 |
| + 2013 Annual Report | 2013-01 to 2026-09 | 165 | 164 | 153 |
| + 2010 to 2012 Annual Report (÷1,000) | 2010-01 to 2026-09 | 201 | 200 | 189 |

All months are consecutive and positive, so eligible changes equal matched months minus one. The "today" row is a replication of the production eligibility rules (consecutive months, partial-source month excluded, positivity), not a production API call.

**Research-only analytics preview (not for the UI or production API; the same Pearson/beta definitions as `copper_kwacha.py`, Copper IMF monthly change versus official USD/ZMW mid change, official mids rounded to 2 decimals):**

| Sample | Changes | Pearson r | Beta (Sxy/Sxx) |
|---|---|---|---|
| 2014-01 to 2026-09 | 152 | -0.040 | -0.043 |
| 2013-01 to 2026-09 | 164 | -0.041 | -0.044 |
| 2010-01 to 2026-09 | 200 | -0.076 | -0.073 |
| 2026-02 to 2026-10 (project observed-days) | 8 | -0.255 | -0.287 |

Reading: the deeper history gives far more usable sample (152 to 200 changes versus 8) and a long full-sample coefficient close to zero, so the current 8-change coefficient is within small-sample noise of a much weaker long-run co-movement. These numbers carry rounding noise from 2-decimal inputs (about 0.025% of level) and are descriptive, not significance or causal claims.

## 17. Rights and access

- All sources above were anonymous, public and non-authenticated; no bypass used.
- No licence, copyright or reuse statement was found in the publications inspected, nor on the site bundle beyond a privacy modal. Redistribution and commercial-use status for BoZ data remain **`review_required`**. Public access is not treated as permission to redistribute. BoZ was not contacted and no email was sent.
- Access characteristics: JSON:API list and file URLs are stable in structure but file names change on republication; PDFs live under `/sites/default/files/migrated/fortnight-statistics/<year>/` (older issues) and `/sites/default/files/<yyyy-mm>/` (newer); there is no documented rate limit; scraping a web UI is not required.

## 18. Final verdict

**APPROVED FOR LOCAL MONTHLY IMPLEMENTATION — RIGHTS REVIEW REQUIRED** (applies to the proposed monthly FX dataset only, not the existing intraday adapter).

- No structured official monthly source exists. The defensible source is the **BoZ Fortnightly Statistics Table 02 / 01A monthly averages: 153 months, 2014-01 to 2026-09, 2 decimals, stable across all overlapping issues sampled**. Annual-report tables extend the series to 2010-01 only with an explicit rebasing policy.
- Design choice for Copper (not made here): **Option B** (official monthly series over the whole available range, as a new methodology version) is preferable to **Option A** (keep the short observed-days sample) once implemented; **Option C** (splicing official history with project-derived recent months) is **not recommended** because definitions are not established to be identical. In the well-covered 2026 months the two agree within 0.03%, but this is observation, not proof.
- The current observed-days method is not wrong: it is an explicitly documented derived method over the stored intraday rows. The question is only whether an official series is preferable for long-horizon monthly analytics.
- Keep intraday `fx_rates` separate; do not insert monthly values into it.
- If Copper later switches FX inputs, publish it as a new methodology id (for example `copper_zm_v2`, and new correlation/beta ids) and leave `copper_zm_v1` and `copper_kwacha_*_v1` unchanged.

## 19. Implementation gates

1. Rights review for BoZ publication data (BoZ contact still unsent; decision on local/internal vs wider use).
2. Product decision on PDF dependence: a fail-loud Table 02 parser with committed small fixtures (offline tests, no live network), accepting the loss of structure if BoZ changes layout.
3. Explicit handling of published anomalies (2022-11, and 2012-04 / 2013-12 if the Annual Report phase is added): validator must flag, and the importer needs a named, documented exception list rather than silent repair.
4. Decide phase scope: 2014-01 onward only, or add 2013 and 2010 to 2012 with a stored ÷1,000 policy and `source_unit` per row.
5. Separate table/migration and a separate methodology id; no change to `fx_rates`, `copper_zm_v1`, `copper_kwacha_corr_v1` or the sensitivity methodology.
6. A refresh policy for the newest months (new fortnightly issue every 2 weeks; the latest month is partial in the publication itself and the daily/weekly blocks are the only intramonth values, so incomplete months must not be imported).
7. Before any production use of the research correlation/beta numbers above, recompute them inside the application under the new methodology id.

Status: deeper FX history is **research in progress**; nothing here is implemented.
