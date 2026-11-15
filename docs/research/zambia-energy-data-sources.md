# Zambia electricity system baseline: source discovery (Energy Phase 1)

Status: **research only**. Nothing here is implemented. No provider, migration, schema, route, frontend component or existing dataset was changed. Research date: 2026-11-15.

## 1. Executive recommendation

- **Canonical source: the Energy Regulation Board (ERB) of Zambia**, in two complementary annual publications that are anonymous, text-layer PDFs (no OCR):
  - the **Energy Sector Report** (ESR), one per year, 2015 to 2025 (11 files found; 10 downloaded and confirmed to have a text layer; the 2016 file is about 17.5 MB and its download timed out, so it is unverified), and
  - the **Statistical Bulletin** (`statBullet`), 2018 to 2025 (8 files found; 2018, 2019, 2023, 2024 and 2025 downloaded and checked for a text layer).
- The newest Statistical Bulletin (2025) is the best single extraction target: it carries **2013-2025 annual generation by station**, a **station-level installed-capacity table** (name, licensee, technology, grid/off-grid, MW), annual exports/imports and a 2015-2025 consumption-by-sector table.
- **Granularity is annual only.** No monthly generation, no monthly peak demand and no machine-readable (API/CSV/XLSX) ERB dataset was found. A first slice must therefore be an **annual** dataset, not a monthly one like the BoZ FX series.
- **ERB values are revised and are not always internally consistent** (section 7). The BoZ discipline applies: store as published, record the publishing report per value, never repair, and fail closed when two figures for the same concept cannot be reconciled by an explicit rule.
- Load shedding has **no usable canonical history** (section 9). Exclude it from Phase 1.
- Everything else (SAPP, IEA, IRENA, World Bank, Ministry of Energy decks) is validation or gap-filling only. Ministry of Energy, ZESCO and the Zambia Statistics Agency were **not assessed in depth** in this pass (section 3).

## 1a. Verification status

**Verified by reading downloaded PDFs (text layer):**
- ERB is the strongest canonical candidate; coverage is annual only.
- 2025 Statistical Bulletin Table 13 (ZESCO large hydro) and Table 14 (ZESCO mini-hydro) were read row by row for 2013-2025. Tables 15-16 (diesel, IPPs, 2013-2025) are known only from the contents page and were **not** read.
- The 2025 bulletin's station capacity register (Table 27), imports/exports (Tables 17-18) and consumption by sector (Table 19, title only for 2015-2025) exist; the 2025 report text confirms 2024-2025 consumption.
- Ten of eleven Energy Sector Reports (2015, 2017-2025) have a text layer; the 2025 bulletin and four other bulletins (2018, 2019, 2023, 2024) do too.
- Historical values are restated between reports (2024 generation), and some figures disagree within a report (section 7).
- Rights: no licence found; status `review_required`.

**Unverified or incomplete (not authoritative findings):**
- The 2016 Energy Sector Report: its download timed out, so existence of the file is known but its content and text layer are not.
- Capacity, deficit and generation-share figures quoted from search snippets (Ministry of Energy deck, a trade-office page, Worldometers and similar).
- Not assessed in depth: SAPP, Ministry of Energy, ZESCO and the Zambia Statistics Agency. Their pages were not read in full, so they are candidates only.
- Bulletins 2020-2022 were not downloaded; bulletin Tables 15-16 and 19 were not read.

## 2. Method and limits

- The assistant's web-fetch proxy refused `erb.org.zm`, `zesco.co.zm` and `zamstats.gov.zm` ("unable to verify domain"), and `moe.gov.zm` failed TLS verification. ERB files were therefore downloaded with plain anonymous GETs (about 17 files, 2 s apart, a descriptive User-Agent). ERB's `robots.txt` disallows only `/wp-admin/`.
- Every number below was read from a downloaded PDF text layer (`pdftotext -layout`) unless marked **(search snippet, unverified)**. Page and table numbers refer to the PDF's printed numbering where visible.
- No PDF content is committed. This document quotes figures only to justify source choices.

## 3. Candidate source inventory

| # | Publisher | Dataset | Format | Coverage seen | Verdict |
|---|---|---|---|---|---|
| 1 | ERB | **Statistical Bulletin** `.../wp-content/uploads/files/statBullet{2018..2025}.pdf` (2022 sits at `.../uploads/statBullet2022.pdf`) | PDF, text layer | 2025 edition: Tables 12-19, 26-27; station generation 2013-2025; consumption 2015-2025 | **canonical** (best extraction target) |
| 2 | ERB | **Energy Sector Report** `.../uploads/files/esr{2015..2025}.pdf`; newest linked as `.../uploads/esr/esr2026.pdf` | PDF, text layer, 108-168 pages | annual narrative plus tables, 2015-2025 | **canonical** for peak demand, energy balance, imports detail, load-shedding appendix; **validation** for the bulletin |
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

- **Canonical**: ERB Statistical Bulletin for generation by station/year, installed capacity by station (latest year only) and imports/exports; ERB Energy Sector Report for peak demand and system energy balance. Each stored value keeps the exact publication it came from.
- **Validation (never supplies rows)**: the *next* ERB report year (which restates the prior year), SAPP statistics, then IEA/IRENA. A validation source may only raise a "differs" flag; it must not overwrite a canonical value.

## 6. Available coverage and metrics

Figures below are quotations to show what each metric's history looks like, not data to load.

| Metric (unit) | Reliable history | Latest | Notes |
|---|---|---|---|
| ZESCO large-hydro generation by station (GWh) | **2013-2025** (bulletin Table 13) | 2025: 8,511.89 | Kafue Gorge, Kariba North Bank, Victoria Falls only; Kafue Gorge Lower and Kariba North Bank Extension are reported elsewhere |
| ZESCO mini-hydro by station (GWh) | 2013-2025 (Table 14) | 2025: 171.37 | |
| ZESCO diesel, IPP generation (GWh) | 2013-2025 (Tables 15-16, titled) | 2025 IPPs: 5,873.17 | Table 15/16 rows not yet read in full |
| **National generation, total (GWh)** | 2022: 19,399.11; 2023: 19,372.92; 2024: 13,324.38 *(ESR 2024)* **or** 13,566.52 *(ESR 2025)*; 2025: 14,560.23 | 2025 | **2024 is restated between reports** (section 7); older totals need the 2015-2021 reports |
| National generation by source group (hydro, solar, coal, diesel/HFO) | per **plant** 2024-2025 (ESR 2025 Table 3-6); technology grouping must be derived from the station register, not inferred | 2025 | no published hydro/solar/thermal GWh subtotal table was found for 2025 |
| Installed capacity, total (MW) | 2018: 2,898.23; 2019: 2,981.31; 2023: 3,812.06; 2024: 3,885.86; 2025: **4,107.86 (ESR Table 3-5) vs 4,118.71 (bulletin Table 27)** | 2025 | stock measure, MW; grid and off-grid reported separately in the ESR |
| Installed capacity by station (MW) | **latest year only** per bulletin (Table 27: numbered rows up to 55) | 2025 | historical station registers exist in each year's bulletin but were not compared |
| Peak demand (MW) | 2021: 2,238.0; 2022: 2,374.5 (all-time peak, 14 Jun 2022 18:30); 2024: 2,299; 2025: 2,204; 2020 report cites 2,310 | 2025 | **definition changes** between "all-time peak with date/time" and "peak national system demand" for the year |
| Imports (GWh) | ZESCO only: 2022: 18; 2023: 294; 2024: 2,467.02; 2025: 2,119.70 | 2025 | power-trader imports in 2025 are given inconsistently (text, list and chart differ) |
| Exports (GWh) | 2019: 1,250.4; 2020: 1,339.53; 2021: 2,150; 2022: 2,923; 2024: 2,555.25; 2025: 2,235.15 (Table 17: ZESCO 1,690.98 + traders 544.17) | 2025 | trader/ZESCO split needed from 2024 on |
| Consumption (GWh, MWh by sector) | 2015-2025 (bulletin Table 19) | 2025: 15,470.6 | ESR 2025 states consumption (15,470.6 GWh) above generation (14,560.23 GWh); it does not reconcile without a clear import/loss bridge, so do not derive balances from it |
| System energy balance (average MW) | **2025 only seen**: average generation 1,488.93, average demand 2,217.52, gap 728.59 | 2025 | ERB's own average-MW "deficit"; not comparable with peak MW |

Earliest/latest usable dates: annual, **2013 (station generation) to 2025**; capacity totals back to 2018 in prose; reports start at 2015. Nothing monthly was found for generation, demand or capacity.

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

First implementation slice (narrow): **ERB 2025 Statistical Bulletin, annual electricity generation by station, 2013-2025** (Tables 13-16). The wider scope below is the eventual Phase 1 target and follows only after that parser is proven.

An **annual** dataset, "ERB Electricity Annual", eventually with:

1. Generation by station and by licensee group, 2013 to the latest bulletin year (bulletin Tables 13-16, then ESR Table 3-6 for 2024-2025 IPP detail).
2. National generation total per report year, with the report that published it.
3. Installed capacity total (MW) per year as published, **plus** the 2025 station register as a separate table.
4. ZESCO and power-trader imports and exports (GWh) per year as published.
5. Peak demand (MW) with the definition recorded (annual peak vs all-time peak, with date/time where printed).

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
   5. comparison of national totals;
   6. tests against the real bulletin.
   Do not start with capacity, load shedding, peak demand, imports/exports, consumption or planned projects.
2. Slice 2: storage and importer for annual station generation, after choosing between a canonical observation with revision history and side-by-side publication vintages (section 13); a stored-only read.
3. Slice 3: installed-capacity totals and the station register, with the explicit reconciliation rules and named exceptions for the 4,107.86/4,118.71 MW difference and the Ndola duplicate.
4. Slice 4: imports/exports and peak demand with definition fields.
5. Slice 5: validation pass against the next ERB report and SAPP; read Ministry of Energy, ZESCO and ZamStats material in full first.
6. Only then: any frontend view, any API cross-dataset analytics.

## 18. Open questions for source review

- Does ERB permit local use and display of its statistical tables, and under what attribution? (Not contacted.)
- Which station-to-technology mapping is accepted for the hydro/solar/thermal split?
- Which rule picks the canonical value when a later ERB report restates an earlier year?
- Are SAPP, Ministry of Energy, ZESCO and ZamStats tables worth adding as validation sources once read?
