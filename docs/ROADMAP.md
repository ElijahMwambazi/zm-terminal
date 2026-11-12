# ZM Terminal roadmap

Implemented work lives in `docs/CURRENT_STATE.md`; this file is only sequencing. Anything under
"Planned" is **not implemented**.

## Completed / current
- BoZ USD/ZMW (live, stored history, manual backfill)
- Treasury Bills (live, stored history, manual backfill)
- Government Bonds (live, stored history, manual backfill)
- Fixed-income history (APIs, charts)
- ERB Zambia fuel prices (this branch: official XLSX archive import, API, UI, `FUEL` commands)

## Next
- ERB Zambia fuel build-ups from the monthly PDFs (excise, margins, levies) and the latest month, which
  the XLSX archive lags

## Planned
- Global oil: Brent + WTI through an approved provider / OpenBB
- Gold provider discovery (licensing first)
- Zambia-derived commodity views: `OIL ZM`; `GOLD ZM` once a gold source is approved
- Zambia sovereign yield curve (needs the stored T-bill and bond history)
- BTC/USD + BTC/ZMW
- Copper
- Cross-market `COMPARE` / `CHART` / `CORR` (after the underlying histories are stable)
