# Copper provider discovery (global copper benchmark)

Decision record, 2026-11-14. **Research only.** No Copper schema, migration, API route, importer, UI, command or provider-policy code exists or was added; nothing was persisted. Scratch downloads (IMF API responses, the World Bank workbook already downloaded for the Gold research) were kept outside the repository. No account, API key or credential was created or used, no email was sent, and no permission was requested. "Contact" in the task brief was read as correspondence; reading public documentation and public unauthenticated data was done, as the task requires. The scripted requests to `api.imf.org` used the client identifier `zm-terminal-research/1.0` (a few small requests, and the IMF structure definition downloaded earlier for the Gold research was reused); public pages were also read through a fetch tool whose client identity was not verified.

Conventions: **Fact** = read or measured on 2026-11-14. **Interpretation** = our reading. **Unresolved** = needs the provider's written answer. Status vocabulary: `acceptable for local research` (an engineering/source-governance classification, not legal advice or a formal licensing determination), `review_required`, `rejected`.

## 1. Objective and benchmark semantics
A global, monthly, USD-per-metric-ton copper benchmark relevant to Zambia's mining economy, to support a future `COPPER` view and history, a derived `COPPER ZM`, and copper-versus-USD/ZMW analysis. Wanted: physical/spot rather than a futures price; monthly period average; a clearly identified grade and underlying market; a stable definition; deep history; provenance suitable for persisted normalized observations. Every candidate is a **global benchmark, not a Zambian copper price**.

## 2. Candidate summary

| Candidate | Series | Basis | Frequency | History | Access | Decision |
|---|---|---|---|---|---|---|
| IMF PCPS | `PCOPP` (USD, monthly) | Copper, grade A cathode, LME spot price, CIF European ports (IMF's own code description) | Monthly average | 1992-M01 to 2026-M10, 418 months, no gaps | Anonymous SDMX 3.0 JSON | **PREFERRED** |
| World Bank Pink Sheet | `Copper` ($/mt) | Copper (LME), grade A, minimum 99.9935% purity, cathodes and wire bar shapes, settlement price | Monthly average | 1960M01 to 2026M10, 802 months | Public XLSX workbook | **BACKUP** |
| FRED | `PCOPPUSDM` | A copy of the IMF series | Monthly | 1992-01 to 2026-09 (one month behind IMF today) | API key requires a FRED account | **Cross-check only** |
| LME direct | Official prices | LME Grade A, cash and 3-month | Daily | Sold as a licensed product | Login / licence | **REJECTED for this implementation** (official pages not readable) |
| Futures (CME/COMEX etc.) | Futures settlements | A futures contract, not a physical/spot benchmark | Daily | Licensed | Licensed | **Not evaluated further** (semantics mismatch) |

## 3. IMF Primary Commodity Prices (PCPS)

### 3.1 Facts: series and access
- Dataflow `IMF.RES:PCPS` on the public SDMX 3.0 API `https://api.imf.org/external/sdmx/3.0/…`. No authentication or key was needed for any request.
- Indicator `PCOPP`, whose code description (read from the API's own structure definition) is: "Copper, grade A cathode, LME spot price, CIF European ports, US$ per metric ton". Name: "Copper, US dollars per metric tonne, Unit prices".
- Dimensions `COUNTRY` (world `G001`), `INDICATOR` (`PCOPP`), `DATA_TRANSFORMATION` (`USD` for the price; `INDEX`, `INDEX_PCH`, `INDEX_PCHY` are derived index views) and `FREQUENCY` (`A`, `M`, `Q`). The price series is `G001.PCOPP.USD.M`. One request for `*.PCOPP.*.*` returned all twelve series in 97 KB.
- Monthly USD series: **418 observations, 1992-M01 (2150.57962901611) to 2026-M10 (14507.97363636364), no gap between consecutive months.** The annual series has 34 values and the quarterly series 139 (through 2026-Q3).
- Values arrive as full-precision decimal strings (up to 12 decimal places observed, for example `14507.97363636364`), with `SCALE` 0. Latest months: 2026-M08 14331.26476190476, 2026-M09 14473.05590909091, 2026-M10 14507.97363636364.
- The observation attribute `DERIVATION_TYPE` is `R` ("Raw data (no derivation)") on every monthly observation in this response. No observation carried an estimate or projection code (`SE`, `SEHI`, `SEME`, `SP`).
- The IMF technical documentation (`pcps-technical-documentation.pdf`, read through a fetch tool) says prices are "converted to monthly frequency by averaging weekly or daily values" and that "All benchmark prices are denominated in USD." It lists "London Metal Exchange" among price-series sources, "and others as specified for individual commodities". The FRED copy's notes say "Prices are period averages in nominal U.S. dollars." **Interpretation:** the monthly value is an average of daily or weekly prices in nominal USD.
- Update cadence: the IMF commodity-prices page says "Primary Commodity Prices data are updated monthly"; no day of the month is stated. By 2026-11-14 the API already carried 2026-M10, while FRED's copy (updated Oct 22) ended at 2026-M09.
- An Excel database link exists on the IMF commodity-prices page (`external-data.xlsx`, "September 2026" edition); it was not fetched.

### 3.2 Revisions and estimates
- The task brief states that IMF documentation says PCPS prices can be revised retroactively and that some series temporarily use estimates for missing recent periods. **This statement was not located in the IMF pages read** (the technical document and commodity-prices page returned no such text), so I can establish neither that it exists for Copper nor that it does not.
- **Source may revise** versus **current Copper values were observed to revise** are kept separate: no revision of Copper was observed. The only comparison available is weak evidence: FRED's copy (updated Oct 22) and the IMF API read today agree exactly for 2026-M04 to 2026-M09 (section 8).
- **Recommendation (not implemented):** treat any monthly value as potentially revisable. Ingest append-only with conflict detection, as oil and BTC do: an already-stored month with a different value is a conflict, never an overwrite. A later revision-aware import (explicit reconciliation with the source version noted) is the proper follow-up if a revision is ever observed.

### 3.3 Facts: IMF terms (https://www.imf.org/en/about/copyright-and-terms, read through a fetch tool; no date shown; DOI 10.5089/9798400235801.081)
- The "Special Terms for Statistical Data" list names "**Primary Commodity Prices**;" explicitly, and covers "Most statistical data available on www.IMF.org, www.data.IMF.org, or the iData Portal that explicitly identify the International Monetary Fund as the source."
- "Users may download, extract, copy, create derivative works, publish, distribute, and use Data" subject to the listed conditions.
- Conditions: "Distributed or reproduced Data must appear accurately with attribution to the IMF as the source"; the citation form "Source: International Monetary Fund, Database Name, <<link to the dataset>>."; "If material transformation occurs, it must be stated explicitly along with the required source citation."; redistributors "agree to take reasonable efforts to communicate and promote compliance by their users with these terms."; "sellers must inform purchasers that the Data is available free of charge from the IMF."
- "For any potential commercial reuse of IMF Data, email copyright@imf.org to request permission."
- Third-party language: "For material not attributed to the IMF, users should obtain permission directly from the respective third-party copyright holder." and "The IMF does not represent or warrant that it owns or controls all rights in all Content".
- "The IMF prohibits bulk download of information by automated technology without explicit permission"; "Copying or downloading IMF Content in any systematic way" needs written permission under the general terms (the statistical-data terms then grant the download/extract permission above); LLM training on IMF content is not permitted; "all other terms set forth in the general terms and conditions continue to apply".
- The data portal's dataset page had no rendered metadata (DOI, licence, access notes, source, methodology were empty in the fetched text), and the guessed SDMX metadata endpoint returned 404. The dataset-level licence and source notes are therefore **unknown**.

### 3.4 The underlying-LME rights question (highest-priority legal question)
- **Fact:** the IMF code description names LME spot prices; the technical documentation lists the London Metal Exchange among sources; the FRED copy carries "Copyright © 2016, International Monetary Fund. Reprinted with permission." and the tag "Copyrighted: Citation Required", with no LME copyright notice.
- **Fact:** PCPS ("Primary Commodity Prices") is named in the IMF's special statistical-data terms, which grant publish/distribute/derivative-work permission with attribution for data that "explicitly identify the International Monetary Fund as the source". No PCPS-specific third-party-rights note was found.
- **Interpretation:** the IMF presents PCPS as IMF Data under the special terms, which is the strongest written grant of any source reviewed. But the IMF's general terms disclaim ownership of all rights, and a series built on LME prices may carry LME rights that the IMF terms do not mention. I could not determine whether the LME origin imposes a separate restriction.
- **Unresolved:** whether normalized redistribution of the PCPS Copper series is covered by the statistical-data terms despite its LME source. This is why the classification stays `review_required` (section 9).

## 4. World Bank Pink Sheet Copper

### 4.1 Facts (the November 2026 workbook downloaded for the Gold research; no new request)
- Sheet `Monthly Prices`, column label exactly `Copper`, unit `($/mt)`, month keys `1960M01` to `2026M10` (802 consecutive months, no missing cells). The workbook is "Updated on November 03, 2026".
- Every value is a whole number (Excel integer), for example 2026-M10 = 14508. The Gold finding of whole-dollar publication applies here too.
- `Description` sheet (Copper row): "Copper (LME), grade A, minimum 99.9935% purity, cathodes and wire bar shapes, settlement price", sources "Bloomberg Finance L.P.; Engineering and Mining Journal; Platts Metals Week; Thomson Reuters Datastream; World Bank." The row carries the workbook's "*" marker and states no historical definition change (unlike Gold, whose note records a June 2025 change). The `Mismatch Details` sheet had no Copper rows in this release.
- The monthly sheet's own title is "monthly prices in nominal US dollars"; the description does not state the averaging rule for Copper, but the Pink Sheet publishes "Monthly Averages".

### 4.2 Rights
- The unresolved World Bank question is the same one recorded for oil and Gold: Data Catalog dataset 0038238 says CC BY 4.0, the Dataset Terms exclude some third-party data, and no page ties the workbook file to the licence. The Copper sources are named third-party vendors (Bloomberg, Platts, Datastream, Engineering and Mining Journal), the same class as Brent, WTI and Gold.
- **Does a future World Bank answer cover Copper?** The existing ticket asks about the workbook, Brent, WTI and Gold. An answer saying the licence applies to the whole workbook or dataset would logically cover Copper, since the column is in the same file, but an answer scoped only to the four named series would not. A Copper-specific clarification would still be prudent if the reply is not stated at workbook level. Nothing was sent; the World Bank ticket status is unchanged (only an automated acknowledgement exists).
- Status: local `acceptable for local research`; public, API and commercial use `review_required`.

## 5. LME direct
- **Not read.** Every `lme.com` request made by the fetch tool returned HTTP 403 (no page body), so the official LME pricing and licensing pages could not be verified. I did not scrape, create an account, or bypass anything.
- Secondary evidence (search-engine summaries of lme.com and datalicensing.lme.com pages, not read directly): showing LME data on a website or application requires a **distribution licence** (fees differ for real-time, 30-minute and next-day delayed data; a next-day website display is described as requiring approval as a licensed distributor); building products or reference values that use LME data as an input can require a **derived data licence**; a **non-display licence** covers other uses; LME historical prices and data are sold for research and market analysis; LME market-data reports are described as free of charge, with Official Prices included in the purchasable historical reports.
- **Interpretation:** direct LME is licence-gated for public display, redistribution, derived use and historical data. For current implementation it is **rejected**: it is not free, the terms could not be read, and a licence would cost money. The IMF/World Bank series already carry the LME-linked benchmark at monthly frequency.
- Local development against direct LME is not recommended (no free historical access was established) and persisted historical storage is not shown to be permitted without a licence.

## 6. FRED `PCOPPUSDM`
- Facts (series page and `data/PCOPPUSDM.txt`, read through a fetch tool): "Global price of Copper (PCOPPUSDM)", source International Monetary Fund, release "Primary Commodity Prices", units "U.S. Dollars per Metric Ton, Not Seasonally Adjusted", monthly, 1992-01-01 to 2026-09-01, "Updated: Oct 22, 2026". Notes: "Value represents the benchmark prices which are representative of the global market.", "Prices are period averages in nominal U.S. dollars.", "Copyright © 2016, International Monetary Fund. Reprinted with permission.", a banner "Data in this graph are copyrighted. Please review the copyright information in the series notes before sharing.", and "Complete terms of use and contact details are available at http://www.imf.org/external/terms.htm."
- FRED API: "All web service requests require an API key", and a key can only be requested after logging into a FRED account. That conflicts with the no-account constraint. A direct request for the public `fredgraph.csv` download timed out twice, so the full FRED history was not downloaded.
- FRED API terms: series "may be owned by third parties and subject to copyright restrictions"; for use beyond personal use "you must contact the data owner" and FRED "cannot give you such permission"; no storage or commercial-use clause was found.
- **Conclusion:** FRED is an access layer over the IMF series, adds no rights (the notes defer to IMF terms), requires an account for API use, and lags the IMF by about a month. Cross-check only; it must not become a shortcut around IMF or LME rights.

## 7. Futures and other alternatives
CME/COMEX copper futures and commercial vendors were not evaluated further: a futures settlement is not the physical/spot benchmark wanted, and vendor feeds are licensed products.

## 8. Cross-source comparison (exact published values)

IMF `PCOPP` (API, 2026-11-14), World Bank `Copper` (November 3 workbook, whole dollars), FRED `PCOPPUSDM` (page read, Oct 22 update, shown to the digits FRED prints):

| Month | IMF | World Bank | FRED |
|---|---|---|---|
| 2026-M03 | 12528.70954545455 | 12529 | not read |
| 2026-M04 | 12890.68772727273 | 12951 | 12890.687727272730 |
| 2026-M05 | 13512.15619047619 | 13543 | 13512.156190476190 |
| 2026-M06 | 13552.04090909091 | 13552 | 13552.040909090910 |
| 2026-M07 | 13542.82086956522 | 13543 | 13542.820869565220 |
| 2026-M08 | 14331.26476190476 | 14326 | 14331.264761904760 |
| 2026-M09 | 14473.05590909091 | 14474 | 14473.055909090910 |
| 2026-M10 | 14507.97363636364 | 14508 | not yet in FRED's copy |

- **IMF equals FRED exactly** (Decimal comparison, ignoring a trailing zero) for the six months FRED printed (2026-M04 to 2026-M09). Only six values were compared because the FRED CSV download timed out.
- **World Bank versus IMF** over the 418 overlapping months (1992-M01 to 2026-M10): median absolute difference 0.052%, maximum 1.21% (60.3 USD in one month). Only 162 of the 418 months are within the 0.5 USD that whole-dollar rounding allows, so **256 months differ by more than rounding**. **Interpretation:** the two are related but not the same series: the IMF describes "LME spot price, CIF European ports", the World Bank "LME … settlement price", and the averaging windows may differ. Their agreement does not prove equivalent methodology or licence status, and equal LME mention is not evidence of equality.
- Latest: IMF 2026-M10 14507.97363636364; World Bank 2026-M10 14508; FRED ends at 2026-M09 (14473.055909090910). LME's daily official price is contextual only and was not read; a daily price is not comparable to a monthly average.

## 9. Rights and licensing matrix

| Use | IMF PCPS | World Bank Copper | LME direct | FRED |
|---|---|---|---|---|
| Local/private research | `acceptable for local research` | `acceptable for local research` | not recommended (not free, terms unread) | n/a (needs an account) |
| Persist normalized monthly values | `acceptable for local research` (the statistical-data terms permit download, extract and copy; no storage clause) | `acceptable for local research` | `rejected` without a licence | n/a |
| Public non-commercial display | `review_required` (explicit grant for Primary Commodity Prices with attribution; LME origin unresolved) | `review_required` | `rejected` without a licence | `review_required` |
| API redistribution | `review_required` (same) | `review_required` | `rejected` without a licence | `review_required` |
| Commercial use | `review_required` (the IMF says to email `copyright@imf.org` for commercial reuse) | `review_required` | `rejected` without a licence | `review_required` |
| Derived data (Copper x USD/ZMW) | `review_required` (derivative works are permitted with attribution and a stated transformation; LME origin unresolved) | `review_required` | `rejected` without a derived-data licence | `review_required` |

Final IMF classification: **`review_required`** (not `permitted`) for every use beyond local research. Reasons: the LME third-party question is unresolved, commercial reuse is expressly permission-gated, the IMF prohibits "bulk download … by automated technology" without permission (a single manual import of one series is probably not "bulk", unconfirmed), and the portal-level licence metadata could not be read.

## 10. Technical-access matrix

| | IMF PCPS | World Bank | FRED |
|---|---|---|---|
| Auth | none | none | API key (account) |
| Format | SDMX 3.0 JSON | XLSX workbook, strict parser exists | JSON/CSV |
| Request cost | one request for the monthly series (~97 KB for all `PCOPP` views) | one workbook (~0.6 MB, shared with oil and Gold) | one request after a key exists |
| Precision | full (12 decimals observed) | whole dollars | full |
| History | 1992 to 2026-M10 | 1960 to 2026-M10 | 1992 to 2026-M09 |
| Existing code | none (a small SDMX adapter would be new) | `worldbank_oil.py` could be extended | none |

## 11. Revisions and historical integrity
Facts and recommendation are in section 3.2. All three sources are complete monthly series without gaps in what was read (IMF 418 months and World Bank 802 months verified as consecutive; FRED's count was not verified). Because the IMF and World Bank definitions differ (section 8), **do not splice or average them**: pick one source for stored history.

## 12. Decision

| Criterion | IMF PCPS | World Bank | LME direct | FRED |
|---|---|---|---|---|
| Semantics | Strong (documented grade, spot, CIF Europe, monthly average) | Good (LME settlement, grade A), averaging rule not stated | Strongest (the primary benchmark) | Copy of IMF |
| Source authority | High (IMF) | High (World Bank) | Primary | Secondary |
| Technical access | Best (anonymous API) | Good (workbook, parser exists) | Gated | Needs an account |
| History | 1992 to now, full precision | 1960 to now, whole dollars | Licensed | 1992 to last month |
| Persistence | Acceptable locally | Acceptable locally | Licence needed | n/a |
| Public display | `review_required` | `review_required` | Licence needed | `review_required` |
| API redistribution | `review_required` | `review_required` | Licence needed | `review_required` |
| Commercial | `review_required` (permission) | `review_required` | Licence needed | `review_required` |
| Derived data | `review_required` | `review_required` | Licence needed | `review_required` |
| Maintenance cost | New small adapter | Extend an existing parser | High | Needs a key |

- **Preferred provider:** IMF PCPS `PCOPP` (strongest written reuse grant, full precision, anonymous API, a documented benchmark definition).
- **Backup provider:** World Bank Pink Sheet `Copper` (reuses the workbook path, but whole dollars, a different definition and an unresolved licence ticket).
- **Cross-check:** FRED `PCOPPUSDM` (not independent; lags the IMF).
- **Rejected:** direct LME for the current implementation (licence-gated and its terms could not be read); futures sources (semantic mismatch).
- Architecture note: `PGOLD` (the IMF backup for Gold) lives in the same IMF dataflow, so one small IMF PCPS adapter could later serve both series. Gold's status is unchanged: it remains blocked on the World Bank answer.

## 13. Proposed future provider policy (not implemented)
For `imf` / `imf_pcps_copper`: `classification` `public_market_data`; `authentication` none (anonymous SDMX); `normalized_storage_allowed` true for local use; `raw_response_storage` disabled; `attribution_required` true ("Source: International Monetary Fund, Primary Commodity Price System, <dataset link>", with any transformation stated); `redistribution` `review_required`; `commercial_use` `review_required` (IMF asks for `copyright@imf.org`); required provenance `source`, `dataset`, `series`, `source_url`, observation period, `retrieved_at`; request guidance: manual import only, one SDMX request for the monthly series, no polling, no bulk download. Revisions: a changed stored month is a conflict. Keep local and public rights separate so a later permission changes policy, not code.

## 14. Future `COPPER` vertical slice (design only)
- Normalized monthly model (a dedicated `global_copper_prices`-style table of the `global_oil_prices` family, not finalised): `provider` (`imf`), `benchmark` (`COPPER`), `period_start`/`period_end` (calendar month), `value` (Decimal USD per metric ton at the published precision, no quantization), `currency` USD, `unit` `metric_ton`, `price_basis` `monthly_average`, `source_series` `PCOPP`, a source definition string, `source_url`, `retrieved_at`. The SDMX responses read carry no per-observation release date (the dataflow's `lastUpdatedAt` is a structure timestamp, not a data release), so `publication_date` would have to be nullable or omitted. A generic commodity table can wait until a third commodity makes the duplication costly.
- UI: heading `COPPER · GLOBAL BENCHMARK` with the sublabel `IMF PCPS · MONTHLY AVERAGE`, showing USD/metric ton and the documented definition "Copper, grade A cathode, LME spot price, CIF European ports". Do not label it "LME Copper" or "Zambia copper"; a card may read `IMF COPPER BENCHMARK`. Command `COPPER`.

## 15. Future derived `COPPER ZM` (design only)
`copper_zmw_per_mt = IMF monthly copper USD/mt x the same-month mean of stored daily BoZ USD/ZMW mids`, mirroring `OIL ZM`: derived, ZMW per metric ton, computed on request from stored inputs, nothing persisted, only actual BoZ observation days (each day equally weighted), with the day and observation counts shown, partial-month disclosure, and no interpolation or fill. Stored BoZ history begins 2026-01-22, so the first derivable month is January 2026 (a partial month); the series cannot start earlier without another authoritative FX source, which should not be added for this.

## 16. Future copper-versus-kwacha analytics (not implemented)
Once the history is stable: copper USD against USD/ZMW, copper in ZMW, rolling correlation and sensitivity. Correlation is not causation, the copper price alone does not determine the kwacha, and Zambian production and export volumes also matter. The international benchmark must stay a separate dataset from Zambia's own copper statistics.

## 17. Zambia production is a separate dataset
Production tonnage, exports, export value and mine or project data are Zambian statistics, to be researched separately (candidate sources include the Zambia Statistics Agency, the Ministry of Mines, the Bank of Zambia and the Ministry of Finance). They are not mixed with the benchmark price and were not researched here.

## 18. OpenBB
The OpenBB image installs `openbb-platform-api`, `openbb-cboe`, `openbb-oecd` and `openbb-fred` only. `openbb-fred` exposes a `commodity/price` router whose spot-price model maps only EIA energy series (WTI, Brent, natural gas, jet fuel and similar), with no copper. A copper series could only be fetched through the generic FRED series endpoint, which needs a FRED API key (an account) and returns a copy of the IMF series with the copyright notes above. OpenBB would be an access layer, not the source or licence holder, so it is no shortcut. **Recommendation:** direct IMF integration. This finding is specific to the installed extensions.

## 19. Unresolved questions
1. Does the IMF statistical-data permission cover normalized storage, public display, API redistribution, commercial use and derived values for the PCPS Copper series despite its LME source? (`copyright@imf.org`)
2. Is a single manual SDMX request for one series "bulk download by automated technology"?
3. Do PCPS revisions or estimates apply to Copper, and are prior months overwritten? (Not located in the IMF documentation read.)
4. Are the portal's dataset-level licence and access notes available anywhere (they did not render)?
5. Why do the IMF and World Bank copper series differ by more than rounding in 256 of 418 overlapping months?

## 20. Implementation gate
**APPROVED FOR LOCAL IMPLEMENTATION — PUBLIC/COMMERCIAL RIGHTS AS NOTED**

- Local/internal development, local testing and local normalized storage of the IMF PCPS `PCOPP` monthly series are acceptable under the IMF's special statistical-data terms (an explicit grant to download, extract and copy Primary Commodity Prices with attribution), with one manual SDMX request for the monthly series.
- Public display, API redistribution, commercial use and public derived `COPPER ZM` remain `review_required` pending the IMF's written answer on the LME third-party question and on commercial reuse; this gate is a source-governance classification, not legal advice.

## 21. Draft email: IMF (NOT SENT)
To: `copyright@imf.org`
Subject: Reuse of the IMF Primary Commodity Price System copper series in a research application

> Hello,
>
> I am building a research application (ZM Terminal) that would store the monthly IMF PCPS Copper series (indicator `PCOPP`, US dollars per metric ton, from the IMF SDMX API) as normalised observations with source and retrieval time, display it with the attribution "Source: International Monetary Fund, Primary Commodity Price System", and may in future expose it publicly and commercially through a web interface and an API. The import would be one manual request for a single series. Could you please confirm:
> 1. that the Statistical Data terms, which list Primary Commodity Prices, cover the PCPS Copper series even though the IMF's code description and technical documentation identify the London Metal Exchange as its source;
> 2. whether a separate LME licence or notice is required when using the IMF-curated series rather than LME data directly;
> 3. that storing normalised historical observations, displaying monthly values publicly with IMF attribution, and exposing them through the application's own API are permitted;
> 4. whether commercial use is permitted or needs written permission;
> 5. whether derived values such as copper USD per tonne multiplied by a USD/ZMW rate may be displayed and distributed with the transformation stated;
> 6. whether one manual request for one series counts as "bulk download … by automated technology"; and
> 7. whether recent monthly values can be revised or estimated and how revisions are published.
>
> The answer to these questions would also help for other PCPS series. Thank you,
> [name]

Status: drafted here only; not sent; the sender name is left blank.

## 22. Evidence
- IMF SDMX API: `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/PCPS/+/*.PCOPP.*.*` and the `DSD_PCPS` structure (read 2026-11-14).
- IMF terms: https://www.imf.org/en/about/copyright-and-terms; commodity prices page https://www.imf.org/en/research/commodity-prices; PCPS technical documentation `…/commodityprices/monthly/pcps-technical-documentation.pdf`; data portal https://data.imf.org/en/datasets/IMF.RES:PCPS.
- World Bank: https://www.worldbank.org/en/research/commodity-markets, workbook `CMO-Historical-Data-Monthly.xlsx` (November 3, 2026 release).
- FRED: https://fred.stlouisfed.org/series/PCOPPUSDM, https://fred.stlouisfed.org/data/PCOPPUSDM.txt, https://fred.stlouisfed.org/docs/api/terms_of_use.html, https://fred.stlouisfed.org/docs/api/api_key.html.
- LME: lme.com returned 403 to the fetch tool; secondary summaries of its data-distribution, non-display and derived-data licensing pages and fee schedules (search results, not read directly).
- Research date: 2026-11-14.
