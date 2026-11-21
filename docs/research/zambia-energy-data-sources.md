# Zambia electricity system baseline: source discovery (Energy Phase 1)

Status: **research only**. Nothing here is implemented. No provider, migration, schema, route, frontend component or existing dataset was changed. Research date: 2026-11-15.

## 1. Executive recommendation

**Assessment.** Based on the sources reviewed so far, the Energy Regulation Board (ERB) of Zambia is the strongest canonical-source candidate for a first electricity dataset. This is a judgement, not a verified fact: SAPP, the Ministry of Energy, ZESCO and the Zambia Statistics Agency have not been assessed deeply enough for an exhaustive ranking (section 1a).

- ERB publishes two annual, anonymous, text-layer PDF series (no OCR needed):
  - the **Energy Sector Report** (ESR), 2015 to 2025 (11 files found; 10 downloaded with a confirmed text layer; the 2016 file is about 17.5 MB, its download timed out, so it is unverified), and
  - the **Statistical Bulletin** (`statBullet`), 2018 to 2025 (8 files found; 2018, 2019, 2023, 2024 and 2025 downloaded and checked for a text layer).
- The **2025 Annual Statistical Bulletin** is the proposed first extraction target (section 19). Its Tables 13-16 give annual generation by station for 2013-2025; this was verified row by row against the pinned file.
- **Granularity is annual only.** No monthly generation, no monthly peak demand and no machine-readable (API/CSV/XLSX) ERB dataset was found. The first slice must therefore be an **annual** dataset, not a monthly one like the BoZ FX series.
- **ERB values are restated and are not always internally consistent** (sections 7 and 19.7). The BoZ discipline applies: store as published, record the publication per value, never repair, and fail closed.
- Load shedding has **no usable canonical history** (section 9). Peak demand is **not yet canonical**: annual-peak and all-time-peak definitions differ between ERB publications and a unified series needs definition reconciliation first (section 6).
- Ministry of Energy, ZESCO, Zambia Statistics Agency, SAPP and IEA/IRENA/World Bank are validation or gap-filling candidates only.

## 1a. Verification status

**Verified by direct reading of downloaded files (text layer):**
- 2025 Annual Statistical Bulletin, pinned in section 19.1: Tables 12-18 and the Table 27 capacity register exist on the pages stated; **Tables 13, 14, 15 and 16 were read row by row** (section 19.2) and footed against their own totals.
- Annual coverage only; no monthly ERB electricity series was found.
- Ten of eleven ESRs (2015, 2017-2025) and five bulletins (2018, 2019, 2023, 2024, 2025) have a text layer.
- Historical values are restated between reports (2024 generation) and some figures disagree within a report (sections 7 and 19.7).
- Rights: no licence found; status `review_required` (section 12).

**Candidate series, known to exist but not row-level verified:**
- Bulletin Table 19 (consumption by sector, titled 2015-2025) and Tables 20-26.
- Earlier-year bulletins (2018-2024) and the ESR generation/capacity/trade tables for 2015-2024, other than the figures quoted in section 6.
- Station capacity register history before 2025.

**Unverified or incomplete (not authoritative findings):**
- The 2016 ESR: the download timed out; content and text layer unknown.
- Capacity, deficit and generation-share figures from search snippets (a Ministry of Energy deck, a trade-office page, Worldometers and similar).
- SAPP, Ministry of Energy, ZESCO and the Zambia Statistics Agency: pages not read in full; candidates only.
- Bulletins 2020-2022 were not downloaded.

## 2. Method and limits

- The assistant's web-fetch proxy refused `erb.org.zm`, `zesco.co.zm` and `zamstats.gov.zm` ("unable to verify domain"), and `moe.gov.zm` failed TLS verification. ERB files were therefore downloaded with plain anonymous GETs (about 17 files, 2 s apart, a descriptive User-Agent). ERB's `robots.txt` disallows only `/wp-admin/`.
- Every number below was read from a downloaded PDF text layer (`pdftotext -layout`) unless marked **(search snippet, unverified)**. Page and table numbers refer to the PDF's printed numbering where visible.
- No PDF content is committed. This document quotes figures only to justify source choices.

## 3. Candidate source inventory

| # | Publisher | Dataset | Format | Coverage seen | Verdict |
|---|---|---|---|---|---|
| 1 | ERB | **Statistical Bulletin** `.../wp-content/uploads/files/statBullet{2018..2025}.pdf` (2022 sits at `.../uploads/statBullet2022.pdf`) | PDF, text layer | 2025 edition: Tables 12-19, 26-27; station generation 2013-2025; consumption 2015-2025 | **canonical candidate** (proposed first extraction target; 2025 edition pinned in section 19.1) |
| 2 | ERB | **Energy Sector Report** `.../uploads/files/esr{2015..2025}.pdf`; newest linked as `.../uploads/esr/esr2026.pdf` | PDF, text layer, 108-168 pages | annual narrative plus tables, 2015-2025 | **canonical candidate** for imports detail and (later) system balance; **validation** for the bulletin; peak demand and the load-shedding appendix are **deferred** (sections 6 and 9) |
| 3 | SAPP (Southern African Power Pool) | Annual Reports 1997-2022; "SAPP Statistics" 2004-2019/20; Monthly Reports; Demand and Supply | PDF | Zambia/ZESCO rows **not read** | **validation-only candidate**, unassessed |
| 4 | Ministry of Energy | presentations, sector profiles (a Nov 2025 deck was seen via search) | PDF | host failed TLS from the research tool | **validation-only candidate**, unassessed |
| 5 | ZESCO Limited | annual/integrated reports, press statements | PDF/HTML | host reachable by curl; not read | **unassessed**; operator figures are inputs to ERB's tables |
| 6 | Zambia Statistics Agency | monthly bulletin, energy statistics | PDF/XLSX? | host reachable by curl; not read | **unassessed** |
| 7 | IEA / IRENA / World Bank | country electricity profiles | HTML/PDF/API | annual, differing definitions | **validation / gap-filling only**, never canonical |
| 8 | News, blogs, aggregators (Worldometers, Global Economy, CEIC, Ember mirrors) | derived | HTML | n/a | **rejected** as canonical |

The ERB `Statistics` page (`https://www.erb.org.zm/statistics`) lists both series above and is the discovery index; it offers no CSV/XLSX/API.

## 4. Source-quality ranking

1. **ERB Statistical Bulletin**: regulator-published, tabular, multi-year station series, station capacity register. Annual only; some tables restate prior years.
2. **ERB Energy Sector Report**: same publisher, richer (peak demand, energy balance, load-shedding appendix) but narrative-heavy and layout varies by year.
3. **SAPP**: authoritative for regional pool statistics, but definitions (generation sent out vs energy balance) must be checked against ERB before any use.
4. **Ministry of Energy / ZESCO**: authoritative primary actors but figures seen via search snippets disagree with ERB (for example Ministry deck 3,877 MW and a US trade-office page 3,985.86 MW against ERB 4,107.86 MW for installed capacity; **search snippets, unverified**). Not usable until read in full.
5. **IEA / IRENA / World Bank**: secondary.

## 5. Proposed canonical and validation sources

- **Canonical candidate (generation by station)**: ERB 2025 Annual Statistical Bulletin, Tables 13-16, for the first slice. Installed capacity, imports/exports and consumption follow in later slices after their tables are row-verified. Peak demand and the system energy balance (ESR) are **deferred**: they are not canonical until definitions are reconciled.
- **Validation (never supplies rows)**: the *next* ERB report year (which restates the prior year), SAPP statistics, then IEA/IRENA. A validation source may only raise a "differs" flag; it must not overwrite a canonical value.

## 6. Available coverage and metrics

Figures below are quotations to show what each metric's history looks like, not data to load. They are grouped by evidence strength.

### Verified historical series (directly inspected, structurally verified)

| Metric (unit) | Coverage | Latest | Notes |
|---|---|---|---|
| ZESCO large-hydro generation by station (GWh), bulletin Table 13 | 2013-2025, 3 stations | 2025: 8,511.89 | foots to its Total column for every year |
| ZESCO mini-hydro generation by station (GWh), Table 14 | 2013-2025, 6 stations | 2025: 171.37 | foots; dash cells present |
| ZESCO diesel generation by station (GWh), Table 15 | 2013-2025, 7 stations | 2025: 1.30 | foots; dash cells present |
| Independent power producer generation by station (GWh), Table 16 | 2013-2025, 14 columns | 2025: 5,875.67 | foots except 2024 (+0.05) and 2025 (+0.01); uses `0`, not dashes |
| National generation total (GWh), Table 12 | 2025 only | 14,560.23 | rows do not foot; see section 19.7 |
| Exports (GWh), Table 17; imports, Table 18 | 2024-2025 only | 2025 exports 2,235.15; ZESCO imports 2,119.70 | read for 2024-2025 only |
| Station capacity register (MW), Table 27 | 2025 only | 4,118.71 | differs from ESR total 4,107.86 (section 7) |

### Candidate historical series (exist, not row-level verified in this pass)

| Metric | Where | Note |
|---|---|---|
| Consumption by economic sector | bulletin Table 19, titled 2015-2025 | contents-page title only; ESR 2025 text gives 2024-2025 totals |
| National generation totals 2022-2025 | ESR 2023 / ESR 2024 / ESR 2025 prose and tables: 2022: 19,399.11; 2023: 19,372.92; 2024: 13,324.38 *(ESR 2024)* or 13,566.52 *(ESR 2025)*; 2025: 14,560.23 | read in the reports, not parsed; **2024 is restated** |
| Installed capacity totals | ESR prose: 2018: 2,898.23; 2019: 2,981.31; 2023: 3,812.06; 2024: 3,885.86; 2025: 4,107.86 | stock, MW; not compared across years |
| Imports and exports before 2024 | ESR prose: exports 2019: 1,250.4; 2020: 1,339.53; 2021: 2,150; 2022: 2,923; imports 2022: 18, 2023: 294 | prose only; trader/ZESCO scope not established |

### Deferred or inconsistent (not canonical)

| Metric | Why deferred |
|---|---|
| **Peak demand (MW)** | ESR 2022 gives an **all-time peak** with date and time (2021: 2,238.0; 2022: 2,374.5, 14 Jun 2022 18:30); ESR 2025 gives "peak national system demand" for the year (2024: 2,299; 2025: 2,204). Annual peak and all-time peak definitions differ between ERB publications. **A unified peak-demand series is deferred; definition reconciliation is required before it can become canonical.** |
| System energy balance (average MW) | 2025 only seen (average generation 1,488.93, average demand 2,217.52, gap 728.59); ERB's own average-MW "deficit", not comparable with peak MW |
| Consumption versus generation | ESR 2025 states consumption (15,470.6 GWh) above generation (14,560.23 GWh); it does not reconcile without an import/loss bridge, so do not derive balances from it |
| Power-trader imports 2025 | text, list and chart differ within one report |

Earliest/latest usable dates for the first slice: annual **2013 to 2025** for the four generation tables (verified). Nothing monthly was found for generation, demand or capacity.

## 7. Units, definitions and known inconsistencies

Keep stock and flow separate and never combine them:

- **Capacity (MW, stock, at year end)**: installed capacity; plant nameplate. ESR reports grid-tied vs off-grid and technology (hydro, solar, thermal coal, thermal LSGO).
- **Energy (GWh/MWh, flow over a period)**: "generation" is **energy sent out** by grid-connected stations (ESR definition); imports, exports, consumption are energy flows. Consumption is stated in MWh by sector in the ESR and GWh in prose.
- **Demand**: peak demand (MW), energy consumption (GWh), and the system "deficit" (average MW, not a peak or an energy quantity).

Observed inconsistencies (each is a reason for fail-closed extraction):

1. **Restatement**: 2024 national generation is 13,324.38 GWh in ESR 2024 but 13,566.52 GWh in ESR 2025 (prose says 13,568). Historical revisions occur. Store each value with its publishing report; never silently replace.
2. **Capacity totals differ in the same year**: 4,107.86 MW (ESR 2025 Table 3-5, coal 415 MW including an 85 MW plant) vs 4,118.71 MW (bulletin Table 27, coal 330 MW; Ndola 110 MW appears once under Diesel and again under Heavy Fuel Oil). The register must not be summed blindly.
3. **Filename year vs report year**: `esr2025.pdf` and `esr2026.pdf` are byte-identical files containing "Energy Sector Report 2025" (published 2026-05-28 per the landing page). Pin each file by SHA-256 and size, as the BoZ importer does, and key it by the report's own printed year.
4. **Reclassification across years**: Kafue Gorge Lower is listed under ZESCO "Major Stations" in ESR 2023 and under Independent Power Producers in ESR 2025.
5. **Peak-demand definition differs** by year (above).
6. **Trade figures** for power traders do not agree within one report (text vs list vs chart).

## 8. Extraction risks

- Layout differs per year; tables are text-layer and parse with `pdftotext -layout`, but column alignment is irregular (wrapped headers, page-break splits in Table 14, a subtotal row inside tables). Per-year parsers with pinned page numbers, as in `boz_annual_table.py`, are the right pattern; a generic table scraper is not.
- Values are printed with varying decimals (`2,155`, `4,274.57`, `0.003`); store as exact `Decimal` strings and record printed precision.
- Footnote and reclassification effects (items 2 and 4 above) require an explicit station-to-technology register rather than inference.
- Charts duplicate some numbers (Figure 3-6, Figure 6); **never read values from charts when a table exists**.
- Appendix and bulletin tables sometimes break across pages; row counts must be pinned and checked.

## 9. Load shedding and supply availability

- ERB ESR 2025 Appendix 22 ("Load Management (Load shedding) Hours") gives **monthly 2025 values** by region and utility, labelled "Customer Lost Hours" in huge integers (for example 259,498,378 for a corporate total in January 2025). It is a customer-hours aggregate, not hours per day or a rationing schedule. No equivalent table was found in ESR 2022-2024 (searched for load-shedding hours, schedule and peak keywords).
- Public schedules (hours per day; press coverage in search results quoted 8 to 21 hours in 2024-2025, unverified) exist only as ZESCO announcements and press coverage; they are inconsistent across regions and dates.
- **Recommendation: no canonical load-shedding time series.** A future, separately labelled dataset could carry ERB Appendix 22 customer-lost-hours **for 2025 onward only**, with its definition spelled out. Do not scrape announcements or social posts into the core dataset.

## 10. Plants

A plant register is **feasible but narrow**: bulletin Table 27 provides, for each numbered row (up to 55), licensee, station, renewable flag, technology, grid/off-grid and installed MW for the latest year. It does **not** provide commissioning date, province or operational status; those must stay null (no inference). Known problems: duplicated Ndola entry, numbering gaps (row 20, 29), off-grid micro rows (0.01 MW) that should be flagged, and the 4,107.86 vs 4,118.71 MW total difference. ESR 2025 text names 2025 additions (Chisamba 100 MW solar, Mailo 25 MW solar, Kafue Gorge Lower Mini 12 MW hydro, an 85 MW coal plant at a fertiliser company of which 40 MW is stated to feed the grid) that the register only partly reflects. A register therefore needs a per-row `source_publication` and a reconciliation note, not a single clean table.

## 11. Planned versus operating

ESR and ministry material list projects under construction and planned projects (for example a Batoka Gorge scheme discussed in ESR 2021). These must be a **separate dataset** with status values (`operating`, `under_construction`, `announced`) and never feed installed-capacity totals. Only rows from the ERB installed-capacity register count as installed.

## 12. Rights and licensing

- The ERB site footer states "Copyright 2026 All Rights Reserved"; no licence, reuse terms or API terms were found on the Statistics page or in the PDFs read. Treat rights as **`review_required`**, the same posture as BoZ: local/internal use only until ERB is contacted (not done). The ERB Act and Electricity Act No. 11 of 2019 (which mandate monthly returns) were not reviewed for data-publication rights.
- SAPP footer: "© sapp.co.zw All rights reserved", no licence seen.
- Do not redistribute the PDFs. Store parsed values, provenance and page references only.

## 13. Provenance design (proposal, not a commitment)

Mirror the BoZ monthly FX provenance:

- `source_name` (`ERB`), `source_publication` (printed title and year), `source_series` (for example `erb_statbulletin_table_13_large_hydro_gwh`), `source_table`, `source_page` (printed and PDF page), `source_period` (calendar year as printed), `source_url`, `source_sha256`, `retrieved_at` (import time, excluded from equality as for FX).
- `value` as a Decimal string, `unit` (`GWh`, `MW`, `MWh`), `published_precision`, `basis` (`observed`, `estimated`, `planned`) and `measure_kind` (`stock` or `flow`).
- **Requirement (not a final key):** ERB republishes and restates historical values, so the implementation must preserve publication vintage and must never silently overwrite an earlier official value. Whether this is a canonical observation plus a revision/vintage history, or side-by-side publication vintages, is an implementation-phase design decision and is deliberately left open here. Any rule that picks a "current" value must be explicit and documented.
- Validation reports never create rows. A mismatch between two ERB reports for the same period is recorded as a restatement, not an error, but a mismatch **within** one report (for example the two 2025 capacity totals) fails closed and needs a named exception, as with the BoZ 2012-04 and 2013-12 ordering exceptions.

## 14. Proposed Phase 1 dataset scope

First implementation slice (narrow): **ERB 2025 Statistical Bulletin, annual electricity generation by station, 2013-2025**, from Tables 13, 14, 15 and 16, whose exact scope, structure and rules are specified in section 19. The wider scope below is the eventual Phase 1 target and follows only after that parser is proven.

An **annual** dataset, "ERB Electricity Annual", eventually with:

1. Generation by station and by licensee group, 2013 to the latest bulletin year (bulletin Tables 13-16, then ESR Table 3-6 for 2024-2025 IPP detail).
2. National generation total per report year, with the report that published it.
3. Installed capacity total (MW) per year as published, **plus** the 2025 station register as a separate table.
4. ZESCO and power-trader imports and exports (GWh) per year as published.
5. Peak demand (MW), **only after** annual-peak and all-time-peak definitions are reconciled; a unified series is deferred and not canonical.

Possible API shape later (research target only): `GET /api/v1/energy/zm/generation?range=...` returning annual observations, not the monthly shape suggested in the brief. Fields such as `hydro_gwh`, `solar_gwh`, `thermal_gwh` need an explicit technology mapping per station and should be derived views, not stored as if published.

## 15. Explicit exclusions

- Monthly generation, monthly peak demand or any series the sources do not publish monthly.
- Load-shedding hours per day, schedules and announcements.
- "Supply deficit" derived by us from demand minus generation; ERB's 2025 average-MW balance is a published figure for 2025 only.
- Installed capacity from search snippets, news or the Ministry/trade-office pages.
- Projects under construction or announced (separate future dataset).
- Chart-derived values, interpolation, forward-fill, or back-casting before 2013.
- IEA/IRENA/World Bank values as stored observations.

## 16. Future analytics (not to be implemented now)

All are descriptive. Correlation is never causation, and the same published-source caveats apply.

- Generation-mix shifts and hydro dependency (hydro share of generation 2013-2025).
- Domestic production versus imports and exports, and net trade, once trader data is reconciled.
- Peak-demand growth against installed capacity (MW against MW only, never against energy).
- Energy balance (average generation against average demand) as published.
- Supply conditions against monthly USD/ZMW and Copper series later, using a stated alignment rule (annual power data against annual aggregates of monthly data).
- Generation against rainfall/hydrology later, once a hydrology source is chosen (ERB reports reservoir levels in charts and narrative only).
- Electricity consumption by mining against copper production later, using the bulletin's sector table.

## 17. Recommended implementation order

1. **Slice 1 (smallest, highest value)**: ERB 2025 Statistical Bulletin, annual generation by station, 2013-2025, with no database:
   1. source manifest;
   2. PDF SHA-256, size, page and table pins;
   3. fail-closed parser;
   4. station/year observation model;
   5. diagnostic comparison of published totals under the discrepancy policy in section 19.7 (not an equality gate);
   6. tests against the real bulletin.
   The exact specification is section 19.
   Do not start with capacity, load shedding, peak demand, imports/exports, consumption or planned projects.
2. Slice 2: storage and importer for annual station generation, after choosing between a canonical observation with revision history and side-by-side publication vintages (section 13); a stored-only read.
3. Slice 3: installed-capacity totals and the station register, with the explicit reconciliation rules and named exceptions for the 4,107.86/4,118.71 MW difference and the Ndola duplicate.
4. Slice 4: imports/exports; peak demand only once its definitions are reconciled.
5. Slice 5: validation pass against the next ERB report and SAPP; read Ministry of Energy, ZESCO and ZamStats material in full first.
6. Only then: any frontend view, any API cross-dataset analytics.

## 18. Open questions for source review

- Does ERB permit local use and display of its statistical tables, and under what attribution? (Not contacted.)
- Which station-to-technology mapping is accepted for the hydro/solar/thermal split?
- Which rule picks the canonical value when a later ERB report restates an earlier year?
- Are SAPP, Ministry of Energy, ZESCO and ZamStats tables worth adding as validation sources once read?

## 19. First-slice parser specification (ERB 2025 Statistical Bulletin, generation by station)

Documentation only; no parser, schema or route exists. Everything in this section was read directly from the pinned file below (PDF text layer via `pdftotext -layout`, plus word coordinates for the Table 16 header).

### 19.1 Pinned source

| Field | Value |
|---|---|
| Publisher | Energy Regulation Board (Zambia) |
| Printed title | "2025 ANNUAL STATISTICAL BULLETIN", cover date "February 2026" (PDF metadata Title "2025 ANNUAL STATISTICAL BULLETIN", Subject "Energy regulation Board STATISTICAL BULLETIN") |
| Publication year | 2025 (statistics year); published 2026 |
| Canonical URL | `https://www.erb.org.zm/wp-content/uploads/files/statBullet2025.pdf` |
| Final URL | the same (HTTP 200, 0 redirects, `application/pdf`) |
| Byte size | **1,193,636** |
| SHA-256 | **`03fa6051ef49ed02aef527d1264c6f389479747dd4b022508f5ae54f3f5e4203`** |
| PDF pages | 42 (A4); PDF page = printed page + 7 for the generation tables |
| Text layer | yes (Word-generated PDF 1.7, tagged); no OCR needed |
| Retrieved | 2026-11-15 (fetched twice; both copies identical) |
| Server headers | `Last-Modified: Fri, 31 Jul 2026 11:40:33 GMT`; `ETag: "1236a4-657e6a8100e30"` |
| PDF dates | created and modified 2026-03-30 |

If ERB serves different bytes in future, the pins must not be replaced silently: record the mismatch, treat it as a new publication vintage and re-review.

### 19.2 Table structure (verified row by row)

All four tables use **year rows and station columns** (a station is a column, not a row). Values are exact decimals with thousands separators; all years 2013-2025 appear in every table (13 year rows each).

| Table | Printed title | PDF page(s) | Printed page(s) | Unit | Years | Station columns | Total col. | Rows |
|---|---|---|---|---|---|---|---|---|
| 13 | "Electricity generation from ZESCO's large hydro power plants (GWh), 2013-2025" | 22 | 15 | GWh | 2013-2025 | 3: `Kafue Gorge`, `Kariba North Bank`, `Victoria falls` | `Total (GWh)` | 13 |
| 14 | "Electricity generation from ZESCO's mini- hydro power plants (GWh), 2013-2025" | 22-23 | 15-16 | GWh | 2013-2025 | 6: `Chishimba Falls`, `Lunzua`, `Lusiwasi Lower`, `Lusiwasi Upper`, `Musonda Falls`, `Shiwan'gandu` | `Total (GWh)` | 13 (8 on p.22: 2013-2020; 5 on p.23: 2021-2025) |
| 15 | "Electricity generation from ZESCO's Diesel power plants (GWh), 2013-2025" | 24 | 17 | GWh | 2013-2025 | 7: `Chama`, `Itezhi tezhi`, `Kabompo`, `Luangwa`, `Lundazi`, `Shang'ombo`, `Zambezi` | `Total (GWh)` | 13 |
| 16 | "Electricity generation from independent power producers (GWh), 2013-2025" | 24 | 17 | GWh | 2013-2025 | 14 (below) | `Total GWh` | 13 |

Notes:
- The printed Table 14 title contains "mini- hydro" with a space. Contents-page titles for Tables 13-16 are not used for identity.
- **Table 14 continues from PDF page 22 to 23**: the **header row repeats** on page 23 (`Year Chishimba Falls ... Total (GWh)`), but the **caption "Table 14" is not repeated**. Page 23 contains only that header and the years 2021-2025, followed by the printed page number 16. Table 15 starts on the next PDF page (24) under its own heading `2.1.3`.
- Tables 13, 15 and 16 are single-page. Table 15 and Table 16 share PDF page 24 (Table 15 ends, section heading `2.1.4`, then Table 16).
- Table 16 has a **multi-line stacked header** in which words are split across 7 printed lines. The ordered list below was extracted from word coordinates (every header word assigned to the nearest data column) and cross-checked against the 2025 values in ESR 2025 Table 3-6; it is the **final pinned list**. Whitespace-only normalization applied: header lines belonging to one column are joined with a single space, in top-to-bottom order. Nothing else is normalized; capitalization and spelling are as printed.
- **Table 16 final ordered columns** (PDF page 24, left to right after `Year`; positions are the ordinal among the 14 station columns):

  | # | Printed header (line-joined) | Role |
  |---|---|---|
  | 1 | `Bangweulu Power Limited` | station observation |
  | 2 | `Itezhi tezhi Power Corporation` | station observation |
  | 3 | `Kafue Gorge Lower` | station observation |
  | 4 | `Kariba North Bank Extension` | station observation |
  | 5 | `Lunsemfwa Hydro Power Limited` | station observation |
  | 6 | `Maamba Energy Limited` | station observation |
  | 7 | `Ndola Energy Corporation Limited` | station observation |
  | 8 | `Kafue Gorge Mini Hydro` | station observation |
  | 9 | `Chisamba` | station observation |
  | 10 | `Solar century` (printed on two lines: `Solar` / `century`) | station observation |
  | 11 | `United Capital Fertiliser` (no `Limited`) | station observation |
  | 12 | `Ngonye Power Limited` | station observation |
  | 13 | `Dangote` | station observation |
  | 14 | `CEC Emergency Generators` | station observation (flagged `group_like`) |
  | - | `Total GWh` (printed `Total` / `GWh`) | **diagnostic total only, never an observation** |

  The `Solar century` identity is the line-joined printed text; ESR 2025 spells the same plant `Solarcentury`, which is a different publication and is **not** used to rewrite the bulletin's identity.
- **Name collision**: Table 15's `Itezhi tezhi` is a ZESCO diesel station; Table 16's `Itezhi tezhi Power Corporation` is the IPP hydro station. They are different stations and must never be merged by name.
- No footnotes, explanatory notes or legend appear on PDF pages 22-24 for these tables.

### 19.3 Final first-slice scope

The first slice is **annual generation by station, 2013-2025**, from **Tables 13, 14, 15 and 16 together**. They are not included merely because they are adjacent: each is a distinct ownership/class group of the same national generation figure that Table 12 summarises.

| Table | Group represented | Station observations | Non-station columns | In canonical station/year dataset |
|---|---|---|---|---|
| 13 | ZESCO large hydro | the 3 station columns | `Total (GWh)` = group total | yes |
| 14 | ZESCO mini hydro | the 6 station columns | `Total (GWh)` = group total | yes |
| 15 | ZESCO diesel | the 7 station columns | `Total (GWh)` = group total | yes |
| 16 | Independent power producers (as classified by the bulletin) | the 14 station columns | `Total GWh` = group total | yes |

No table is dropped. Per-table coverage was checked and is the same (2013-2025) for all four, so the slice claim of 2013-2025 holds. The parser must still assert coverage per table and must not infer it.

### 19.4 Column classification

- **Station observation**: every named station column in Tables 13-16 (3 + 6 + 7 + 14 = 30 columns). `CEC Emergency Generators` is a column in Table 16 but is plant-group-like ("generators"); it is kept as a published column and flagged `group_like`, not split or renamed. The bulletin's own register (ESR 2025 Table 3-6) classes it under LSGO rather than IPPs (section 19.7).
- **Group/subcategory total**: `Total (GWh)` in Tables 13-15 and `Total GWh` in Table 16.
- **National total**: none of these tables prints one. Table 12 (PDF page 22, "National Electricity Generation 2025") is a separate 2025-only summary and is **not** part of the slice's observations.
- **Metadata/non-data**: the `Year` column.

The parser-only first slice emits station/year observations only. Group totals are read for **validation diagnostics only** and are never emitted as station rows.

### 19.5 Missing-value semantics

- **Token**: a missing cell in Tables 14 and 15 is the ASCII hyphen-minus `-` (U+002D); no en dash, blank or other token was seen. Table 13 has no missing cells. **Table 16 uses the printed numeral `0` and no dashes.**
- **The source defines none**: no legend or note explains `-` or `0` on these pages.
- **Policy (conservative)**: a printed numeric `0` (Table 16) is the numeric value `0`, as published. A `-` (Tables 14-15) is **missing / not reported**, never converted to zero. Because Table 16's `0` cannot be told apart from "not operating yet" or "not reported", it must not be interpreted as a different state than the one the table prints; the parser preserves `0` exactly and does not infer commissioning dates from it.
- The publication does not let us distinguish "zero generation", "not reported" and "not applicable" for `-`. All `-` cells are recorded as one state: *not reported in the source*.
- Group totals are numerically `sum of printed station values` where a station is `-` (the dash behaves as zero **in the source's totals**, so the total must be checked by summing present values only).

### 19.6 Expected table identity and failure conditions

Identity for each table is its exact normalized caption, its page(s), its ordered column list and its year list. A future parser must **fail closed** on:

- a wrong table number, or a modified/missing caption (caption text compared after whitespace normalization);
- a table on an unexpected PDF page, or a missing continuation page for Table 14 (page 23 must exist and begin with the repeated header, no caption);
- Table 14's page 23 header differing from page 22's header;
- an unexpected, missing, extra or reordered station column in any table (including the Table 16 order above);
- a missing, extra, duplicated or non-increasing year; the first year must be 2013 and the last 2025, with 13 consecutive rows;
- a cell that is neither a decimal nor `-` (and `-` only in Tables 14-15); a Table 16 cell that is neither a decimal nor `0`;
- a row whose **source value cells** differ from the expected count, independent of whether a cell is numeric or `-`/`0`: excluding the `Year` cell, each row must have the station cells (3, 6, 7 and 14 for Tables 13, 14, 15, 16) plus exactly one total cell, so 4, 7, 8 and 15 value cells. Every station cell counts whether numeric or dash; dash cells never reduce the expected row width, and the total cell is counted separately for structural validation;
- scanning text elsewhere in the PDF for similar tables: only the pinned pages are read.

### 19.6a Complete-grid invariant

The four tables together define a complete logical grid of **390 station/year observation cells**: (3 + 6 + 7 + 14) = 30 station columns x 13 years (2013-2025). The parser must verify the whole expected station/year grid (every expected station for every expected year exists exactly once) rather than counting emitted numeric values. Dash cells are part of the grid and are emitted (`generation_gwh = null`, `value_state = not_reported`); they are never omitted. A numeric cell becomes a Decimal, and a printed numeric zero becomes Decimal zero. Total cells are outside the 390.

### 19.7 Total reconciliation and the Table 16 versus Table 12 discrepancy

Published totals checked against sums of the printed station values:

| Table | Result |
|---|---|
| 13, 14, 15 | every year foots exactly (all 39 rows) |
| 16 | every year foots exactly **except 2024 (printed 6,096.15; sum of printed columns 6,096.10; +0.05) and 2025 (printed 5,875.67; sum 5,875.66; +0.01)** |

Table 16 versus Table 12 (2025):

- Table 16 `Total GWh`, 2025: **5,875.67 GWh**, summing 14 columns including `CEC Emergency Generators` 2.50.
- Table 12 (printed "Generation (GWh)"), 2025 rows: `ZESCO's Major-Hydro` 8,511.89; `ZESCO's Mini-Hydro` 171.37; `ZESCO's Diesel` 1.30; **`IPPs` 5,873.17**; `Total` 14,560.23.
- **5,875.67 - 2.50 = 5,873.17 exactly** (arithmetic fact): Table 12's `IPPs` figure equals Table 16's 2025 total minus the `CEC Emergency Generators` value. Separately, ESR 2025 Table 3-6 classifies `CEC emergency Generators` under "LSGO" (2.50 with Shang'ombo 1.30 = 3.80 for 2025), not under IPPs.
- Table 12's rows **do not foot to its own total**: 8,511.89 + 171.37 + 1.30 + 5,873.17 = 14,557.73, but the printed total is 14,560.23 (+2.50). Using Table 16's 5,875.67 instead gives exactly 14,560.23. This is a footing discrepancy printed in Table 12 itself.
- The same pattern appears in 2024 (Table 16 6,096.15; CEC 5.95; ESR Table 3-6 IPP subtotal 6,090.15, a 0.05 residual).

**Conclusion and policy:**
1. **Verified facts:** Table 16's 2025 total is 5,875.67 GWh; Table 12's `IPPs` value is 5,873.17 GWh; the difference is 2.50 GWh; Table 12's printed total has a corresponding +2.50 GWh footing discrepancy; and ESR 2025 classifies `CEC Emergency Generators` under LSGO/diesel rather than IPPs.
   **Supported inference (not source-proven):** the matching 2.50 GWh and ERB's classification strongly suggest that Table 12's composition or scoping treatment involves `CEC Emergency Generators`, with Table 16 grouping them under IPPs and Table 12's `IPPs` row not. Table 12 itself does not enumerate its constituent stations, so this causal explanation is **not** established by the source and must not be recorded as fact.
2. Neither figure is repaired. Table 12 and Table 16 are both stored/diagnosed as published; the parser records the discrepancy rather than forcing equality.
3. National/group total comparison is a **diagnostic**, not an equality gate: it reports the footing residuals above and the Table 16/Table 12 difference as a *known pinned source discrepancy* (with the supported, unproven CEC inference above), and fails only if a residual **changes unexpectedly from the recorded values** (so any re-served file with different numbers is caught).
4. Table 12's own footing error (+2.50) is a source discrepancy to be flagged, not corrected.

### 19.8 Proposed manifest entry (documentation only)

```
source_id:            erb_statbulletin_2025
role:                 canonical parser source (first slice, candidate)
publisher:            Energy Regulation Board (Zambia)
title:                2025 ANNUAL STATISTICAL BULLETIN (cover: February 2026)
url:                  https://www.erb.org.zm/wp-content/uploads/files/statBullet2025.pdf
sha256:               03fa6051ef49ed02aef527d1264c6f389479747dd4b022508f5ae54f3f5e4203
size_bytes:           1193636
pdf_pages:            42
text_layer:           yes
retrieved:            2026-11-15
rights_status:        review_required
pdf_to_printed_page:  printed = pdf - 7 (tables 13-16)
tables:
  - id: 13, pdf_pages: [22], printed_pages: [15], unit: GWh, years: 2013-2025,
    title: "Electricity generation from ZESCO's large hydro power plants (GWh), 2013-2025"
  - id: 14, pdf_pages: [22, 23], printed_pages: [15, 16], unit: GWh, years: 2013-2025,
    title: "Electricity generation from ZESCO's mini- hydro power plants (GWh), 2013-2025"
  - id: 15, pdf_pages: [24], printed_pages: [17], unit: GWh, years: 2013-2025,
    title: "Electricity generation from ZESCO's Diesel power plants (GWh), 2013-2025"
  - id: 16, pdf_pages: [24], printed_pages: [17], unit: GWh, years: 2013-2025,
    title: "Electricity generation from independent power producers (GWh), 2013-2025"
diagnostic_only_tables:
  - id: 12, pdf_pages: [22], printed_pages: [15], role: 2025 national summary, never emitted as observations
```

### 19.9 Proposed parser output (in-memory only)

For each station column and year, one observation:

```
observation_year, station_name, station_group (large_hydro | mini_hydro | zesco_diesel | ipp),
generation_gwh (Decimal | null), value_state (published | not_reported),
source_table, source_pdf_page, source_printed_page,
source_url, source_publication, source_sha256, publication_year, column_flags (e.g. group_like)
```

**Missing cells are emitted with `generation_gwh = null` and `value_state = not_reported`**, not omitted. Reason: omission would make a parser bug (a dropped row) indistinguishable from a source dash, the complete station-by-year grid is part of what the parser verifies, and a later vintage comparison needs the cell to exist. A printed `0` is emitted as `generation_gwh = 0, value_state = published`. Group totals and the `Year`/`Total` columns are not emitted as observations; they are returned only in a separate diagnostics structure with the recorded residuals. This is not a database schema.

### 19.10 Fail-closed parser invariants

- Exact source SHA-256 and byte size match the manifest; PDF page count is 42.
- Each table is found on its pinned page(s) with its exact normalized caption; Table 14 continues on page 23 with a repeated, identical header and no caption.
- Ordered columns match the pinned lists (Table 16 by the order in 19.2).
- Years are exactly 2013-2025, strictly increasing, one row each, no duplicates, none missing or extra.
- Every numeric cell parses exactly with `Decimal` (no float), thousands separators removed; the printed decimals are preserved.
- `-` appears only in Tables 14-15 and `0` only as a printed Table 16 numeral; neither is converted to the other and neither is interpolated.
- The complete 390-cell station/year grid is present exactly once (30 stations x 13 years), including dash cells emitted as null/`not_reported`; row structure is validated by source value-cell count, not by the number of numeric values.
- No duplicate (year, station) observation; station names are unique within a table and are not merged across tables (the Itezhi tezhi collision).
- Totals are never treated as stations and never copied into the station observations.
- Published totals are never repaired or silently reconciled; the known residuals (+0.05 in 2024 and +0.01 in 2025 for Table 16, and the Table 12 / Table 16 relationship in 19.7) are recorded as expected diagnostics, and any other residual fails.
- No scanning outside the pinned pages; no values taken from charts.
