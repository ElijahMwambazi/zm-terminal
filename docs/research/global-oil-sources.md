# Global oil benchmark sources (Brent, WTI)

Decision record, 2026-11-14. The oil slice stores **monthly** Brent and WTI averages from the World Bank Pink Sheet.

## Evaluated and set aside: FRED / EIA daily spot
- FRED series `DCOILBRENTEU` and `DCOILWTICO` (USD per barrel, daily) were reachable through OpenBB's `fred` provider and a first implementation was built and tested offline.
- During source evaluation, the applicable FRED API terms and EIA source notes did not provide a sufficiently clear fit for ZM Terminal's persisted-history design, so the implementation uses the World Bank Pink Sheet instead. These were observations from the evaluation done for this feature, not general statements about those providers, and they are not re-verified here. This is a fit-for-use decision, not criticism of FRED or EIA, which remain fine sources for other uses; OpenBB and its FRED provider stay installed.

## Selected: World Bank Commodity Price Data (The Pink Sheet)
- Discovery page: https://www.worldbank.org/en/research/commodity-markets, which links "Monthly prices" to `https://thedocs.worldbank.org/en/doc/<hash>/related/CMO-Historical-Data-Monthly.xlsx` (the hash changes each release; verified link on 2026-11-14: hash `74e8be41ceb20fa0da750cda2f6b9e4e-0050012026`, 587,760 bytes, last-modified 2026-11-03).
- Sheet `Monthly Prices`; header-row labels `Crude oil, Brent` and `Crude oil, WTI`; unit row `($/bbl)`; month keys `1960M01`..`2026M10`; missing cell marker `…` (WTI starts 1982M01); cells formatted `0.0`.
- Descriptions from the workbook's `Description` sheet: "Crude oil, UK Brent 38` API" and "Crude oil, US, West Texas Intermediate (WTI) 40` API" (sources listed there: Bloomberg Finance L.P., Energy Intelligence Group, OPEC, World Bank).
- Cross-check: workbook values for Aug/Sep/Oct 2026 (Brent 90.9/116.8/125; WTI 82.7/96.9/97.7) match the PDF "Pink Sheet" edition of 2026-11-03.
- Cadence: updated about the 2nd business day of the month (World Bank Data Catalog, "Commodity Prices - History and Projections", dataset 0038238, metadata updated 2025-05-14).
- Licence: the catalog entry (https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections) states Creative Commons Attribution 4.0 and classification Public, and the Commodity Markets page links the monthly workbook. Not yet established: that the catalog licence applies to that workbook. Open points: the workbook has no licence text, the catalog entry lists its periodicity as annual, and the Pink Sheet PDF footer says "Official Use Only". Confirm with data@worldbank.org before public redistribution or commercial deployment (not a blocker for local development or the code PR); the provider policy keeps `review_required` until then.
- Revisions: the workbook has a `Mismatch Details` sheet comparing "Current" and "Uploaded" cells (header row: Period, Commodity, Current Cell/Display, Uploaded Cell/Display, Difference Type), and observed historical values may differ across releases. The importer treats a changed stored month as a conflict and never overwrites.
- Note: catalog dataset 0037798 (Global Economic Monitor) was looked at first by mistake and is **not** this dataset; its licence text was not used.
