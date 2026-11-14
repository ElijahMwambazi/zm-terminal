# Gold provider discovery (global gold benchmark)

Decision record, 2026-11-14. **Research only.** No gold adapter, table, route, command or UI exists or was added. Nothing was persisted. Scratch downloads (the World Bank workbook and Pink Sheet PDF, IMF API responses) were kept outside the repository and are not committed.

Conventions: **Fact** = read directly from an official source on 2026-11-14. **Interpretation** = our reading of facts. **Unresolved** = needs the provider's written answer.

## 1. Outcome

**PREFERRED SOURCE — LICENCE CONFIRMATION REQUIRED.** The World Bank Pink Sheet monthly Gold series is the preferred source (technically cheapest, same workbook and parser as Brent/WTI, one licence question covers oil and gold). It is **not** approved for persisted, redistributed or commercial use yet, because the Gold series lists third-party commercial data vendors as sources and the Dataset Terms carve third-party data out of the CC BY grant. IMF PCPS is the backup and carries the same class of open question (its Gold series is an LBMA fixing). LBMA/IBA is rejected without a licence; FRED no longer carries the LBMA series.

## 2. Evaluation matrix

| Candidate | Authority | Frequency | History | Machine-readable | Auth | Persist allowed | Redistribution | Commercial use | Underlying third party | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| World Bank Pink Sheet (`Gold`, workbook `CMO-Historical-Data-Monthly.xlsx`) | World Bank Prospects Group | Monthly average (published ~2nd business day) | 1960M01 to 2026M10 (802 months, no gaps) | XLSX (strict-parseable) | None | Catalog says CC BY 4.0; applicability to this series unconfirmed | `review_required` | `review_required` | **Yes, named**: Bloomberg Finance L.P., Kitco.com, IMF IFS, London Bullion Market, Metals Week, Platts Metals Week, Shearson Lehman Brothers, Thomson Reuters Datastream | **PREFERRED, licence confirmation required** |
| IMF PCPS (`PGOLD`) | IMF Research Dept. | Monthly (also quarterly, annual) | 1992-M01 to 2026-M10 (418 months) | SDMX 3.0 JSON, anonymous | None observed | "Statistical Data" terms permit download/extract/derivative works with attribution; no storage clause | `review_required` | `review_required` (IMF asks for `copyright@imf.org`) | **Yes**: series described as the LBMA fixing | **BACKUP** |
| LBMA Gold Price / IBA | LBMA (owner), ICE Benchmark Administration (administrator) | Twice daily | Historical tables on gated MyLBMA | Gated | Licence + portal | No, without an IBA licence | No, without an IBA licence | Licence required | n/a (it is the benchmark) | **REJECTED without licence** |
| FRED (former LBMA gold series) | St. Louis Fed (mirror) | Daily (formerly) | n/a | API | API key | n/a | n/a | n/a | LBMA/IBA | **REJECTED**: series removed |
| Other candidates | none investigated | | | | | | | | | Nothing found that materially improves the decision; no generic commercial gold APIs, finance-site feeds or scrapers were considered |

## 3. World Bank Pink Sheet

### 3.1 Facts: series
- Discovery page: https://www.worldbank.org/en/research/commodity-markets. Release verified 2026-11-14: workbook `…/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx` (587,760 bytes) and `CMO-Pink-Sheet-November-2026.pdf`. The hash changes every release, as the existing oil discovery code already assumes.
- Workbook sheets: `Mismatch Details`, `Monthly Prices`, `Monthly Indices`, `Description`, `Index Weights`. Title row: "World Bank Commodity Price Data (The Pink Sheet)"; row 2: "monthly prices in nominal US dollars, 1960 to present"; row 3: "(monthly series are available only in nominal US dollars)"; row 4: "Updated on November 03, 2026" (the same row layout the oil parser already validates).
- Sheet `Monthly Prices`, header row 5 (label row), unit row 6, data from row 7. Column label exactly **`Gold`** (sits between `Zinc` and `Platinum`), unit cell exactly **`($/troy oz)`** (the Pink Sheet PDF prints `$/toz` and defines "toz = troy oz", so `toz` = troy ounce is confirmed by the official legend).
- Month keys `1960M01`..`2026M10`, 802 consecutive months, **no missing cells for Gold** (the `…` marker, used by WTI before 1982, does not occur). The PDF uses `..` for not available.
- Cell values are **whole numbers (Excel integers)**: all 802 Gold cells are integers (e.g. 2026M10 = 4105, 2026M09 = 4319). Published precision is whole US dollars per troy ounce. Pre-1970s values are the same (1960M01 = 35).
- The Pink Sheet PDF (3 Nov 2026) gives monthly averages to the same precision; Oct 2026 = 4,105 matches the workbook.
- `Mismatch Details` sheet (a "Current vs Uploaded" cell comparison of this release against the previous upload) listed 156 rows in this release and **no Gold rows**; its affected commodities were Barley, Fish meal, LNG Japan, Natural gas index, Palm kernel oil, Rapeseed oil, Sorghum, Soybean meal, Soybean oil, Soybeans, Sunflower oil. This shows that the World Bank does revise past months (for other series) between releases.

### 3.2 Facts: definition of the Gold series, including a methodology break
- Workbook `Description` sheet, Gold row: "**Gold, spot average of daily rates, from June 2025; previously (UK), 99.5% fine, London afternoon fixing, average of daily rates**".
- The same row's Sources column: "Bloomberg Finance L.P.; Kitco.com; International Monetary Fund, International Financial Statistics; London Bullion Market; Metals Week; Platts Metals Week; Shearson Lehman Brothers, Metal Market Weekly Review; Thomson Reuters Datastream; World Bank."
- Pink Sheet PDF description page: "Gold, Gold, spot average of daily rates." (the PDF text is the abbreviated form; the London-fixing wording is in the workbook note).
- **The series definition changed in June 2025**: months to May 2025 are averages of the London afternoon fixing; June 2025 onward are averages of daily *spot* rates. The workbook is one continuous column with no flag on the rows. Overlap with the IMF series (section 4) shows no visible jump, but the definitions differ, so this must be disclosed to users if the history is ever charted.
- Units and basis: US dollars per troy ounce, monthly average of daily rates, nominal.
- **Provenance finding:** the workbook names data vendors and market sources (Bloomberg, Kitco, Thomson Reuters Datastream, IMF IFS, London Bullion Market) as a list. It does **not** say which supplied which period, and it does not name LBMA or ICE Benchmark Administration as the licensor of the series; "London Bullion Market" appears only as one entry in the source list. No provider may be inferred beyond that list.
- Release cadence: "Updated on November 03, 2026". The World Bank Data Catalog entry for dataset 0038238 says updated about the 2nd business day of the month; its own metadata update date is 2025-05-14 (from the earlier oil research).
- Structure stability: the layout is the one the oil parser already validates (title, subtitle, "Updated on", exact-label header, unit row, `YYYYMmm` keys, consecutive months). Gold would be located by exact label `Gold` and exact unit `($/troy oz)`. The existing parser would fail closed on any change. Note the Brent/WTI unit cell is `($/bbl)`.

### 3.3 Facts: licence documents
- Data Catalog dataset 0038238, "Commodity Prices - History and Projections" (https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections): "Classification: Public"; "License: Creative Commons Attribution 4.0"; contact `data@worldbank.org`. The page shows no third-party restriction, no per-series note and **no mention of the workbook or of Gold/Brent/WTI**. (The page says "Downloads (0)".)
- Dataset Terms (https://www.worldbank.org/ext/en/legal/terms-conditions/datasets):
  - "Use of data derived from the Datasets, which may appear in formats such as tables and charts, is also subject to these Dataset Terms."
  - "You may extract, download, and make copies of the data contained in the Datasets, and you may share that data with third parties according to these terms of use. Unless specifically labeled otherwise, these Datasets are provided to you under a Creative Commons Attribution 4.0 International License (CC BY 4.0), with the additional terms below." (the additional terms are a mediation/arbitration clause.)
  - Attribution: "The World Bank: Dataset name: Data source (if known)." Sub-licensees must be required to carry the same acknowledgment (the URL of the terms suffices).
  - "You may use our application programming interfaces (“APIs”) to facilitate access to the Datasets, whether through a separate Web site or through another type of software application."
  - **Exception:** "Some datasets and indicators are provided by third parties, and may not be redistributed or reused without the consent of the original data provider, or may be subject to terms and conditions that are different from those described above. Where applicable, these conditions are included in the dataset or indicator metadata."
  - Questions or "other licence terms": `data@worldbank.org`.
- Data Catalog "Data Access and Licensing" page: CC BY 4.0 "allows users to copy, modify and distribute data in any format for any purpose, including commercial use", and is the default "for all Datasets produced by the World Bank itself and distributed as open data".
- The Commodity Markets page links the workbook and the Dataset Terms page but states no licence on the workbook itself.
- The World Bank general Terms and Conditions say site content is for "informational and non-commercial purposes only, unless otherwise stated", and that Dataset Terms prevail on conflict.

### 3.4 Licence analysis (explicit versus inferred)

| Question | Explicit | Inferred / unresolved |
|---|---|---|
| Does the catalog licence apply to `CMO-Historical-Data-Monthly.xlsx`? | The dataset is "Commodity Prices - History and Projections"; the Commodity Markets page publishes the workbook. | No page ties the workbook file to dataset 0038238 or its licence. **Unresolved** (already noted for oil). |
| Does it cover the Gold series? | Nothing singles Gold out or exempts it. | Gold's source list names several commercial vendors, and the Dataset Terms exclude third-party data. The catalog metadata states no third-party condition, which weakly supports "no override", but "where applicable" metadata may simply be incomplete. **Unresolved.** |
| Persisting normalised monthly values in our database | CC BY: "extract, download, and make copies". | Storing normalised values seems within "copies", provided the third-party exception does not bite. |
| Displaying them in a public app | Terms: derived "tables and charts" are covered by the Dataset Terms. | Probably fine under CC BY with attribution; depends on the exception. |
| Redistribution via a ZM Terminal API | "share that data with third parties"; APIs may facilitate access. | An API is redistribution of the data. Fine under CC BY if the exception does not apply. |
| Commercial use | CC BY allows commercial use (catalog licence page). | The general site terms say non-commercial "unless otherwise stated". Conflict is resolved in favour of Dataset Terms, but only if the Dataset Terms/CC BY apply. |

Not established: that any of the listed vendors (Bloomberg, Kitco, Thomson Reuters Datastream, London Bullion Market/LBMA) has consented to downstream reuse of the Gold figures. Not claimed either: that Gold is restricted merely because the historical definition referred to a London fixing; after May 2025 the series is defined as a spot average, not a fixing.

### 3.5 World Bank licence decision

| Use | Decision | Reason |
|---|---|---|
| Local/private ZM Terminal use | `approved` | Dataset Terms expressly allow extract/download/copy; private research use of a free published workbook creates no redistribution or commercial exposure. This matches what the oil slice already does. |
| Public non-commercial deployment | `review_required` | CC BY and Dataset Terms point to "yes with attribution", but the Gold sources are named third-party vendors and the workbook-to-dataset link is unstated. |
| Commercial deployment | `review_required` | CC BY allows it in principle; the third-party exception and the general site terms' non-commercial default make it unsafe to assume. |
| API redistribution | `review_required` | Same reasons; an API is the clearest redistribution case. |

## 4. IMF Primary Commodity Price System (PCPS)

### 4.1 Facts: series and access
- Official source: https://data.imf.org/Datasets/PCPS (now `https://data.imf.org/en/datasets/IMF.RES:PCPS`). The portal returned `403 Access Denied` to plain scripted GETs (CDN bot protection); we did not try to work around it. The page text was read with a normal fetch tool. Its description: "Indices of market prices for fuel and non-fuel commodities, and actual market prices for fuel and non-fuel commodities."
- A public SDMX 3.0 API was reachable **without authentication or a key**: `https://api.imf.org/external/sdmx/3.0/…` (dataflow `IMF.RES:PCPS` version 9.0.0, structure `DSD_PCPS` 10.0.0; dataflow `lastUpdatedAt` annotation 2025-06-16). The legacy host `dataservices.imf.org` did not resolve from this machine.
- Dimensions: `COUNTRY` (world = `G001` in this response), `INDICATOR`, `DATA_TRANSFORMATION`, `FREQUENCY`. Observation attributes include `PRECISION`, series attributes `SCALE`, `DECIMAL_DISPLAYED`.
- Gold indicator code **`PGOLD`**: name "Gold, US dollars per troy ounce of gold, Unit prices"; description (from the structure codelist, **read directly**): "Gold, Fixing Committee of the London Bullion Market Association, London 3 PM fixed price, US$ per troy ounce". Series used: `G001.PGOLD.USD.M` (data transformation `USD`, monthly). Also published as `A` (annual, 1992 to 2025) and `Q` (quarterly, 1992 to 2026-Q3) and as index/percent-change transformations.
- Monthly history: 418 observations, `1992-M01` to `2026-M10`; latest values `2026-M08` 4419.4157…, `2026-M09` 4326.9841…, `2026-M10` 4102.3664… (observation values are returned as decimal strings with full precision, e.g. `354.700958251953`). Values are **monthly averages of the daily fixing** (derived: the monthly figure is not a whole-dollar value and differs from any single fixing), not an end-of-period fixing. The IMF record does not state the aggregation in the text we read; treat "monthly average" as interpretation until IMF metadata is read.
- The PCPS portal page did not show the metadata panel; the London 3 PM statement above comes from the API's own structure definition, not the portal text. Update cadence is not stated on the page; the monthly data reached 2026-M10 on 2026-11-14.
- Research traffic was minimal: three structure/data requests, one of them the full `DSD_PCPS` structure (2.6 MB). No production client was written.

### 4.2 Cross-check against the World Bank Gold series
Over the 418 overlapping months (1992-M01 to 2026-M10) the World Bank whole-dollar value and the IMF value differ by a median of 0.055%, a mean of 0.09% and a maximum of 0.745% (2011-M12: 1640 vs 1652.31). Oct 2026: 4105 vs 4102.37. The two series agree closely but are not identical; the World Bank series is the rounded one. This supports the plausibility of both and does not settle the licence.

### 4.3 Facts: IMF terms (https://www.imf.org/en/about/copyright-and-terms; no "last updated" date shown)
- General Content: "Free non-systematic downloading and/or printing of Content for personal, noncommercial usage only" … "without any right to resell, redistribute, compile, or create derivative works." Written permission is needed for "Copying or downloading IMF Content in any systematic way."
- **Statistical Data** has its own terms, whose list explicitly includes "Primary Commodity Prices". For Data that "explicitly identify the International Monetary Fund as the source": "Users may download, extract, copy, create derivative works, publish, distribute, and use Data" subject to conditions: accurate attribution ("Source: International Monetary Fund, Database Name" plus a link), explicit statement of any material transformation, a duty to tell purchasers that standalone-sold Data is available free from the IMF, and "as is" provision.
- "For any potential commercial reuse of IMF Data, email copyright@imf.org to request permission."
- "users should obtain permission directly from the respective third-party copyright holder" for material not attributed to the IMF, and "The IMF does not represent or warrant that it owns or controls all rights in all Content."
- "The IMF prohibits bulk download of information by automated technology without explicit permission." There is no clause about storing or caching data. LLM training on IMF content without permission is not allowed (not relevant here).

### 4.4 IMF licence decision

| Use | Decision | Reason |
|---|---|---|
| Local/private | `approved` | Personal non-commercial use is permitted; "Primary Commodity Prices" are listed as Statistical Data that may be downloaded and copied. |
| Public non-commercial | `review_required` | Terms permit publishing and distribution with attribution, but `PGOLD` is itself a London Bullion Market Association fixing, and IBA states that real-time and historical benchmark data needs a licence (section 5). The IMF does not say whether PCPS Gold carries an IBA permission. |
| Commercial | `review_required` | IMF explicitly directs potential commercial reuse to `copyright@imf.org`. |
| API redistribution | `review_required` | "distribute" is permitted for Statistical Data, but the third-party question applies, and an automated API client touches the "bulk download by automated technology" prohibition (a single manual import is probably not "bulk"; unconfirmed). |

A clarification email to the IMF is drafted below and is **not sent**.

## 5. LBMA Gold Price / ICE Benchmark Administration

Facts from https://www.lbma.org.uk/prices-and-data/lbma-gold-price (and its disclaimer and Precious Metal Prices pages) and https://www.ice.com/iba/lbma-gold-price (no dates shown; the licensing email addresses on these pages are obfuscated and were decoded from page attributes, so verify before use):
- "The LBMA Gold Price is administered independently by ICE Benchmark Administration (IBA)"; LBMA owns the benchmark.
- "Any party using the LBMA Gold Price for valuation and pricing activities" needs "a usage licence with IBA"; the benchmark is "available under licence from IBA (including for valuation and pricing activities and for use in transactions)".
- "A licence from IBA is required in order to obtain, use or redistribute real-time or historical benchmark data." The historical tables "have moved to the MyLBMA Portal" and need the IBA licence.
- Nothing was signed up for, no MyLBMA access was attempted, and no gated table was scraped.

Decision: **rejected without a licence** for every ZM Terminal use except reading the public page in a browser: historical data and redistribution require an IBA licence. Interpretation: the licence regime is relevant for IMF `PGOLD` (a named LBMA fixing) and, for pre-June-2025 history, possibly for the World Bank column. It is a reason for the written clarifications, not by itself a reason to reject the intermediaries.

## 6. FRED

Facts: FRED published a notice, "ICE Benchmark Administration Ltd (IBA) Data To Be Removed From FRED" (January 10, 2022): "On January 31, 2022, FRED will no longer include data from ICE Benchmark Administration Limited (IBA)", and the listed series from those datasets "will be deleted from the FRED database", including the LBMA Gold Price daily prices. The two individual series URLs (`GOLDAMGBD228NLBM`, `GOLDPMGBD228NLBM`) returned that notice page when fetched, so removal is confirmed by the notice but the series pages themselves were not independently observed.
FRED API terms: series "may be owned by third parties and subject to copyright restrictions"; for uses beyond personal use "you must contact the data owner" and FRED "cannot give you such permission"; no clause on storing or caching data; no commercial-use clause.
Decision: **rejected/unsuitable** for gold. The earlier oil decision to set FRED aside is not revived by this finding. This research is not a reversal of anything.

## 7. Other candidates
None investigated. Nothing in the official-organisation, exchange or government space was found that materially improves the decision within this task's scope. Rejected categories, per the task: anonymous APIs, finance-site scraping, convenience feeds without reuse terms, unlicensed benchmark mirrors, anything needing CAPTCHA or login circumvention.

## 8. Draft email: World Bank (NOT SENT)

To: `data@worldbank.org`
Subject: Licence clarification: Pink Sheet monthly workbook (dataset 0038238) for stored and publicly served commodity values

> Hello,
>
> I am building an open research application (ZM Terminal) that reads monthly commodity prices from the World Bank Commodity Price Data (The Pink Sheet), file `CMO-Historical-Data-Monthly.xlsx`, linked from https://www.worldbank.org/en/research/commodity-markets. The Data Catalog entry for "Commodity Prices - History and Projections" (dataset 0038238) lists Creative Commons Attribution 4.0, and the Dataset Terms say third-party data may be excepted.
>
> The application stores normalised monthly observations (series name, unit, month, value, publication date, source URL, retrieval time) in its own database. It does not redistribute the Excel workbook. It displays the values with attribution and may in future be accessible publicly, and possibly commercially, through a web interface and an API.
>
> Could you please confirm whether the CC BY 4.0 licence listed for dataset 0038238 applies to:
> 1. the file `CMO-Historical-Data-Monthly.xlsx`;
> 2. the monthly Brent crude oil series;
> 3. the monthly WTI crude oil series;
> 4. the monthly Gold series;
> 5. storing normalised historical observations from those series;
> 6. displaying them publicly;
> 7. exposing those normalised observations through an application or API; and
> 8. commercial use of the above?
>
> Please also tell me whether any of these series (the workbook's Description sheet lists sources such as Bloomberg Finance L.P., Energy Intelligence Group, OPEC, Kitco.com and Thomson Reuters Datastream) are subject to third-party rights that override the catalog licence, and if so what permission or wording is required. Finally, the Gold series note says it changed in June 2025 from the London afternoon fixing to a spot average of daily rates; could you confirm that the monthly value is a simple average of daily spot rates and where the methodology is documented?
>
> Thank you,
> [name]

Status: drafted in this document only; not sent. The sender name is left blank for the user.

## 9. Draft email: IMF (NOT SENT; send only if the IMF path is pursued)

To: `copyright@imf.org`
Subject: Reuse of IMF Primary Commodity Price System gold prices in an open research application

> Hello,
>
> I am building an open research application (ZM Terminal) that would store the monthly IMF PCPS Gold series (`PGOLD`, US dollars per troy ounce, monthly) from the IMF SDMX API (`api.imf.org`) as normalised observations with source and retrieval time, display them with the attribution "Source: International Monetary Fund, Primary Commodity Price System", and may in future expose them publicly and commercially through a web interface and an API. The import would be a single manual request for one series.
>
> Could you confirm (1) that this reuse is covered by the Statistical Data terms for Primary Commodity Prices; (2) whether a manual single-series import counts as "bulk download by automated technology"; (3) whether the PCPS Gold series, described as the LBMA/London 3 PM fixing, is subject to third-party rights (ICE Benchmark Administration/LBMA) that restrict storing, displaying or redistributing it, and (4) what permission is needed for commercial reuse?
>
> Thank you,
> [name]

## 10. Preferred architecture if the World Bank source is approved (not implemented)

- **Reuse the existing World Bank workbook discovery, download and parser** (`worldbank_oil.py`) rather than a second downloader. The Gold column is in the same sheet and the same release; the discovery page, download, host/size/content-type checks, "Updated on" parsing and exact-label column lookup apply unchanged. The parser would need Gold as a third labelled series with its own unit (`($/troy oz)`), and should keep failing closed on any label, unit or layout change.
- Proposed normalised observation: `benchmark = GOLD`, `period_start`, `period_end` (calendar month), `value` (Decimal, published whole dollars), `currency = USD`, `unit = troy_ounce`, `price_basis = monthly_average`, `provider = world_bank`, `source_series = "Gold"`, exact release workbook URL, stable source page URL, `publication_date` (workbook "Updated on"), `retrieved_at`. Also record the methodology break (spot average from June 2025; London fixing before) as provenance metadata or documentation, not as a silent column.
- Storage model: `global_oil_prices` (migration 009) hard-codes `benchmark IN ('BRENT','WTI')` and `unit = 'barrel'`, so Gold does not fit it without loosening those checks. The smallest compatible change is **a dedicated `global_gold_prices` table of the same shape** (`provider = 'world_bank'`, `unit = 'troy_ounce'`), leaving Brent/WTI untouched. A generalised commodity table is attractive only once a third commodity (copper, per the roadmap) makes the duplication costly; do not refactor oil pre-emptively. The table schema is not finalised here and depends on approval.
- Revision handling: follow the oil rule (a changed stored month is a conflict, never an overwrite). The Gold column had no mismatch in the current release, but other columns did.
- Attribution to carry in the UI: "The World Bank: Commodity Prices - History and Projections: Pink Sheet" plus, if the source list is known, the data sources the World Bank names.

## 11. Future `GOLD ZM` (design note only)
If a monthly USD per troy ounce source is approved, the derived indicator would be `Gold USD/toz × BoZ USD/ZMW observed-days average`, using the same FX aggregation as `OIL ZM` (`usd_zmw_observed_days_avg`, with `fx_coverage` partial/observed-days flags for sparse BoZ months). It would be: derived, monthly, ZMW per troy ounce, **not** a Zambian retail jewellery price, **not** a local dealer quote, and **not** an official BoZ gold price. A mixed-definition warning for the June 2025 methodology break would be needed. Not implemented and not scheduled.

## 12. Oil licensing connection
This research does **not** change the existing World Bank oil provider policy; Brent/WTI stay `review_required` for redistribution and commercial use.
- New evidence: the workbook's Description sheet attributes Brent and WTI to named third-party sources (Bloomberg Finance L.P., Energy Intelligence Group, OPEC, World Bank), the same class of third-party attribution as Gold. This makes the third-party exception just as relevant to oil as to gold.
- Still unestablished: that dataset 0038238's CC BY 4.0 applies to the workbook file and to these third-party-sourced series. The single email above asks both questions. A written response should precede any change to `approved`.
- `OIL ZM` continues to inherit this status (Brent is its input).

## 13. Final recommendation

### Preferred provider
World Bank Commodity Price Data (The Pink Sheet), monthly `Gold` ($/troy oz, whole dollars, monthly average of daily rates, spot-based from June 2025). Reasons: already-validated workbook and parser, no authentication, 1960 to 2026-10 history, no missing months, a CC BY 4.0 catalog entry with an explicit contact, and the same clarification covers the oil slice.

### Backup provider
IMF PCPS `PGOLD` via the anonymous SDMX API: 1992-M01 to 2026-M10, full-precision decimal strings, consistent definition (LBMA London 3 PM fixing average) and "Primary Commodity Prices" named in the IMF's Statistical Data terms. Weaker on the LBMA/IBA underlying-benchmark question, commercial reuse (explicit `copyright@imf.org` request), and the automated-download clause. It would need a new adapter.

### Rejected providers
- LBMA Gold Price / IBA: a licence is required to obtain, use or redistribute historical data.
- FRED LBMA gold series: removed from FRED (January 2022 notice).
- Anything scraped, mirrored or fed by unlicensed convenience APIs: not considered.

### Licensing status
- World Bank: local/private `approved`; public non-commercial, commercial and API redistribution all `review_required`.
- IMF: local/private `approved`; public non-commercial, commercial and API redistribution all `review_required`.
- LBMA/IBA: `rejected` without a licence. FRED: `rejected`.

### Blocking questions
1. Does dataset 0038238's CC BY 4.0 cover `CMO-Historical-Data-Monthly.xlsx` and the Gold (and Brent/WTI) series, or do third-party rights apply (World Bank, `data@worldbank.org`)?
2. What is the exact methodology and licensor basis of the post-May-2025 spot-average Gold series (same email)?
3. (Only if the IMF backup is pursued) Does PCPS Gold carry LBMA/IBA restrictions, and is a one-off import "bulk" (`copyright@imf.org`)?

### Recommended next action
Send the World Bank email (section 8) after the user reviews and signs it; do not start `GOLD` until a written answer is received. Meanwhile the roadmap moves to the next item. If the World Bank declines or restricts Gold, draft and send the IMF email (section 9) and reconsider the IMF backup. Persist nothing for gold until a source is approved.

**Final state: PREFERRED SOURCE — LICENCE CONFIRMATION REQUIRED**
