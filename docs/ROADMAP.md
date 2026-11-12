# ZM Terminal roadmap

Implemented work lives in `docs/CURRENT_STATE.md`; this file is only sequencing. Anything under
"Next" or "Planned" is **not implemented**.

## Completed
- BoZ USD/ZMW (live, stored history, manual backfill)
- Treasury Bills (live, stored history, manual backfill)
- Government Bonds (live, stored history, manual backfill)
- Fixed-income history (APIs, charts)
- ERB fuel price history (XLSX archive import, API, UI, `FUEL` commands)

## Current
- ERB latest-month + fuel build-ups (this branch: monthly PDF discovery and strict parsing, `zambia_fuel_build_up_items`,
  `/fuel/build-up`, pump and wholesale build-up views). Press-statement retail prices are ingested only from text PDFs;
  scanned statements (e.g. November 2026) are reported, not read.

## Next
- **Zambia agriculture price discovery / vertical slice**
  - Commodities: `MAIZE`, `SOYA_BEANS`
  - Potential official / regulated sources:
    - ZAMACE: exchange / spot-market price
    - Ministry of Agriculture: national average market price
    - FRA: procurement / policy price
  - **Critical rule: FRA procurement prices must never be presented as market prices.**
  - Planned commands: `AGRI ZM`, `MAIZE ZM`, `SOYA ZM`, `MAIZE MARKET`, `SOYA MARKET`, `MAIZE FRA`
  - Proposed data model, distinguishing price kinds rather than one "price" column:
    `commodity`, `market_type = spot | national_average | procurement`, `provider`, `source_unit`, `price_zmw`,
    `normalized_zmw_per_kg`, `observed_date`

## Planned
- Global oil: Brent + WTI through an approved provider / OpenBB
- `OIL ZM` (Brent converted at the BoZ USD/ZMW rate)
- Zambia sovereign yield curve (needs the stored T-bill and bond history)
- Gold provider discovery (licensing first)
- `GOLD` / `GOLD ZM` once a gold source is approved
- BTC/USD + BTC/ZMW
- Copper
- Cross-market `COMPARE` / `CHART` / `CORR` (after the underlying histories are stable)
