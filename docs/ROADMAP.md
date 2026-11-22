# ZM Terminal roadmap

Implemented work lives in `docs/CURRENT_STATE.md`; this file is only sequencing. Anything under
"Next" or "Planned" is **not implemented**.

## Completed
- BoZ USD/ZMW (live, stored history, manual backfill)
- Treasury Bills (live, stored history, manual backfill)
- Government Bonds (live, stored history, manual backfill)
- Fixed-income history (APIs, charts)
- ERB fuel price history (XLSX archive import, API, UI, `FUEL` commands)
- Commercial bank USD/ZMW rates (BoZ table: parser, storage, manual exact-date importer, stored-only API, `BANK FX` UI; local/internal, rights `review_required`)
- ERB latest-month retail prices + fuel build-ups (monthly PDF discovery, strict text parsing, narrow OCR fallback, stored build-up API/UI)
- Zambia agriculture source discovery (`docs/research/zambia-agriculture-sources.md`)
- Ministry maize + soya national-average vertical slice (Layout B, Apr 2025-Jan 2026, stored API/history/UI and `AGRI` commands)
- Global oil: Brent + WTI monthly averages from the World Bank Pink Sheet (catalog licence CC BY 4.0; applicability to the workbook still to be confirmed), stored API/history/UI and `OIL` commands (FRED/EIA daily spot was evaluated and set aside because it did not provide a sufficiently clear fit for ZM Terminal's persisted-history design)
- `OIL ZM`: derived monthly Brent-in-ZMW indicator (stored World Bank Brent monthly average x the mean of stored daily BoZ USD/ZMW mid-rate averages over the observed days; sparse BoZ months stay derivable and carry their day/observation counts; January 2026 is marked partial because the stored source begins on 22 January), stored-only API, UI and `OIL ZM` command; derived on request and never persisted; available only where both stored histories overlap (from January 2026)
- Yield-curve spreads: four fixed derived published-tenor spreads (10Y-364D, 15Y-364D, 10Y-2Y, 15Y-91D; long yield minus short yield in basis points, with both endpoint auction dates and a mixed-vintage flag), stored-only API and `CURVE ZM` section. Only these four are done; no general curve analytics, interpretation or history exists
- Zambia sovereign yield curve, modelled as the **derived published-auction-yield tenor curve**: the latest stored accepted BoZ T-bill (91D/182D/273D/364D) and Government-bond (2Y/3Y/5Y/7Y/10Y/15Y) auction yield per published tenor bucket at or before an as-of date, stored-only API, `CURVE ZM` / `YIELD CURVE ZM` view with an AS OF control; mixed auction vintages are shown, nothing is interpolated, fitted or bootstrapped, no maturity dates exist, and nothing is persisted
- BTC provider discovery (`docs/research/btc-provider-discovery.md`): **Bitstamp BTC/USD spot preferred; approved for local implementation, public rights pending** (a Bitstamp Data License Agreement is stated for commercial use and unclear for public use). The owner chose to implement locally while the Bitstamp Terms of Use remain unread (see the research document's implementation decision update); no direct BTC/ZMW market source was identified among the evaluated providers. The implementation is recorded in the next bullets
- BTC/USD + live derived BTC/ZMW, **local/internal implementation**: Bitstamp BTC/USD SPOT (live last trade, stored completed daily OHLCV with a manual `backfill_btc` import and a conflict-detecting append-only table), indicative BTC/ZMW derived from Bitstamp BTC/USD and the latest stored BoZ USD/ZMW mid, `BTC` / `BTC USD` / `BTC ZMW` commands and market cards. Public display, API redistribution and commercial use remain `review_required` pending Bitstamp's written clarification or readable terms
- Historical derived BTC/ZMW (`GET /api/v1/btc/zmw/history`, `btc_zmw_daily_v1`), **local/internal**: computed on request from stored Bitstamp daily closes and stored BoZ USD/ZMW only (completed UTC-day close x the arithmetic mean of that Africa/Lusaka date's stored BoZ mids); dates without a BoZ observation are omitted and counted (no forward-fill or interpolation); nothing is persisted and there is no migration; the first derived date is set by the stored BoZ history (2026-01-22 locally); shown as a `DERIVED DAILY HISTORY` section of the `BTC ZMW` view. The inputs are not simultaneous and the series is not a live quote or an executable price; public rights remain `review_required`
- Gold provider discovery (`docs/research/gold-provider-discovery.md`): **World Bank preferred; licence clarification pending.** World Bank Pink Sheet monthly `Gold` ($/troy oz) is the preferred source and IMF PCPS `PGOLD` the backup; LBMA/IBA and FRED rejected. Research only: no gold code, table, route or UI exists.
- Copper provider discovery (`docs/research/copper-provider-discovery.md`): **IMF PCPS `PCOPP` preferred** (monthly average, USD per metric ton, defined as copper grade A cathode, LME spot price, CIF European ports; 1992-M01 to 2026-M10; anonymous SDMX API); World Bank Pink Sheet Copper is the backup, FRED a cross-check, direct LME rejected. **Approved for local implementation; public display, API redistribution, commercial use and derived `COPPER ZM` remain `review_required`** pending the IMF's answer on the underlying-LME question. The implementation is recorded in the next bullet
- Global Copper benchmark, **local/internal implementation**: IMF PCPS `G001.PCOPP.USD.M` (monthly average, nominal USD per metric ton; copper, grade A cathode, LME spot price, CIF European ports), manual strict one-request importer, append-only `global_copper_prices` with conflict detection (migration 011), stored-only `GET /api/v1/copper/latest|history`, `COPPER` command, dashboard card, watchlist value and a monthly history chart. Public display, API redistribution, commercial use and public derived use remain `review_required` (the IMF clarification email is unsent); World Bank Copper stays a research-only backup; the derived `COPPER ZM` is the next bullet
- `COPPER ZM`, **local/internal derived implementation** (`copper_zm_v1`): IMF PCPS Copper monthly average × the mean of stored BoZ daily USD/ZMW mid averages across observed days (each day weighted equally, the same FX method as `OIL ZM`), computed on request from PostgreSQL with nothing persisted and no migration; sparse months stay derivable, availability is the overlap of the stored histories (partial source window flagged), `GET /api/v1/copper/zm/latest|history`, the `COPPER ZM` command, a `COPPER ZM · DERIVED` card and a detail/history view. Not an official series or a Zambia copper price; public display, API redistribution, commercial use and public derived use remain `review_required`
- Copper–Kwacha comparison (`COPPER FX`, alias `COPPER KWACHA`), **local/internal descriptive view** over the COPPER ZM history: COPPER USD, USD/ZMW and COPPER ZMW on the same matched months, each indexed to 100 at the first matched month of the range (visualization only), with a values table. No new API, migration or persistence; no correlation or regression metric yet; public rights unchanged (`review_required`)
- Copper–Kwacha rolling correlation, **local/internal descriptive statistic** (`copper_kwacha_corr_v1`): Pearson r between month-over-month fractional changes of Copper USD and USD/ZMW (not levels, not COPPER ZMW), consecutive calendar months only, excluding the partial FX source-start transition and nonpositive analytical inputs, full-sample r (N >= 6) and 6M/12M rolling windows of consecutive eligible changes, computed on request by `GET /api/v1/copper/kwacha/correlation` with exclusion counts; no persistence, significance test, strength label or causal claim; public rights unchanged (`review_required`)
- Copper–Kwacha historical sensitivity (`copper_kwacha_beta_v1`), **local/internal descriptive statistic**: full-sample beta = covariance of monthly USD/ZMW changes with Copper USD changes / variance of Copper changes, on exactly the eligible sample of the correlation (shared eligibility, N >= 6, undefined for constant Copper changes), computed on request by `GET /api/v1/copper/kwacha/sensitivity`; descriptive only (no intercept, R², significance test, rolling beta or causal claim); public rights unchanged (`review_required`)
- Official BoZ monthly USD/ZMW averages (phase 1), **local/internal implementation**: Fortnightly Statistics Table 02 "Monthly Averages" (buy / sell / published mid, ZMW per USD) from 2014-01, vetted explicit publication manifest, strict text-layer parser, overlap-conflict checks, separate `fx_monthly_averages` table (migration 012), manual `backfill_fx_monthly` importer and stored-only `GET /api/v1/fx/USDZMW/monthly`. Rights remain `review_required`; no frontend, no Copper methodology change
- Zambia CPI/inflation source discovery (`docs/research/zambia-cpi-inflation.md`): **ZamStats Monthly Bulletin PDFs are the canonical source; no usable machine-readable endpoint; BoZ's inflation JSON feed is a cross-check only.** Current regime `2009 = 100` (since the January 2012 bulletin; the pre-2012 CPI is a different `1994 = 100` regime and is never spliced); rights `review_required`. Research only: no CPI code, table, route or UI exists


## Current
- No active implementation branch. The sovereign yield curve and spreads, the local BTC slice (live and stored BTC/USD, live and historical derived BTC/ZMW), the local Copper slice (IMF PCPS monthly benchmark) with the derived `COPPER ZM` view and the `COPPER FX` comparison and the gold, BTC and copper provider research are done or merged; gold implementation is blocked on the World Bank answer and public Bitcoin deployment on Bitstamp's.

## Next
- **1. Gold licence confirmation (blocker):** obtain World Bank written clarification for dataset 0038238 covering the monthly workbook, Gold, Brent and WTI (drafted, unsent: `docs/research/gold-provider-discovery.md` section 8); the Gold series also changed definition in June 2025
- **2. `GOLD` vertical slice, only if that clarification permits it:** not approved and not started; no gold code until then
- **Before any public deployment of copper data:** obtain the IMF's written clarification for public display, API redistribution, commercial use and derived values given the series' LME origin; the IMF email is drafted and unsent (`docs/research/copper-provider-discovery.md` section 21)
- **Before any public deployment of Bitcoin data:** obtain Bitstamp's written clarification (or a Data License Agreement) for public display, API redistribution and commercial use; the Bitstamp email is drafted and unsent (`docs/research/btc-provider-discovery.md` section 7)
- **Before public deployment:** confirm with data@worldbank.org that the World Bank Data Catalog licence (CC BY 4.0, dataset 0038238) applies to the monthly Pink Sheet workbook used for oil; `OIL ZM` inherits Brent as an input, so it must not be described as freely redistributable until then. Not a blocker for development or the code PR.

## Planned
- Zambia CPI/inflation, **Phase 1 approved for local/internal implementation** (not started; rights `review_required`): seven source-published national series on the `2009 = 100` regime only (CPI index and monthly inflation for All Items, Food and Non-Food; annual inflation for All Items), conditional on a vetted publication manifest and layout-family contract before any real import. Annual Food/Non-Food inflation, divisions, provincial series, the old `1994 = 100` regime and derived analytics are deferred; public deployment waits for ZamStats' written terms
- `GOLD` / `GOLD ZM` once the World Bank (preferred) or IMF (backup) gold source is approved in writing; not started
- Rolling Copper-versus-kwacha sensitivity and regression only if justified, lead/lag research only if later approved, Zambian copper production/export research, and a generic cross-market `CORR` framework
- Deeper BoZ USD/ZMW history, remaining phases (phase 1, 2014-01 onward official monthly averages, is implemented; see `docs/research/boz-deeper-fx-history.md`): the 2010-2013 Annual Report backfill (the phase-2 foundation - migration 013, Poppler extraction, Table 13 parser, month-based ÷1,000 normalization, the two named ordering exceptions and the 2014 validation bridge - is implemented; the combined 2010-2026 importer and the widened monthly API read are implemented and tested on disposable databases; applying migration 013 and the 48 rows to the normal database remains a manual step); an official-monthly-FX frontend view; `copper_zm_v2` with new correlation/beta methodology ids (`copper_zm_v1` and `copper_kwacha_*_v1` stay unchanged); rolling beta/regression only if later justified
- Further yield-curve analytics: historical spread charts, inversion interpretation, steepening/flattening, curvature, PCA, real-yield curves, term premium
- `OIL ZM` versus ERB petrol/diesel comparisons (lagged comparisons or spreads); `OIL ZM` is not a pump price and is not combined with ERB fuel anywhere today
- Cross-market `COMPARE` / `CHART` / `CORR` (after the underlying histories are stable)

## Long-term expansion

These are exploratory directions, not approved implementation requirements. They do not replace or reorder the existing roadmap.

ZM Terminal should remain a Zambia-focused economic, market and public-data research terminal. A new domain should move into active development only after its source, reuse constraints, observation semantics, provenance requirements, historical depth and maintenance cost have been reviewed.

Calculated indicators, inferred values and forecasts must remain distinguishable from official observations.

### Zambia economic core

#### Macroeconomics
Potential coverage:
- CPI and inflation
- GDP and sector contributions
- interest rates and monetary policy rate
- government debt
- government revenue and expenditure
- imports, exports and trade balance
- foreign reserves
- industrial production
- employment and labour-market indicators
- economic release calendar

Likely sources include Bank of Zambia, Zambia Statistics Agency and the Ministry of Finance and National Planning.

#### Economic benchmarks and signature metrics

Add a compact benchmark layer that gives Zambia's market data broader economic context without treating long-run or derived indicators as live market prices.

Potential benchmark families:
- purchasing power parity (PPP) conversion factors
- GDP per capita, nominal and PPP
- price-level ratios
- real policy rates
- debt-to-GDP and fiscal-balance-to-GDP ratios
- current-account-to-GDP
- reserve import cover
- terms of trade
- yield-curve slope
- unemployment-plus-inflation "misery" measures
- copper export dependence
- insurance penetration
- business density

Potential Zambia-specific derived metrics:
- PPP FX Gap: market USD/ZMW versus a clearly identified PPP conversion factor
- Copper Export Dependence: copper exports as a share of total merchandise exports
- Kwacha-Copper Relationship: regression or rolling sensitivity (the descriptive rolling correlation and full-sample sensitivity are implemented)
- Energy Dependence: electricity imports as a share of relevant supply
- Business Density: operating establishments per 1,000 residents
- Insurance Penetration: gross written premium relative to GDP

PPP rules:
- keep World Bank/ICP PPP and IMF/WEO implied PPP series separate; do not combine vintages or methodologies
- distinguish economy-wide PPP from household-consumption PPP and other PPP concepts
- label PPP as a long-run purchasing-power benchmark, not a live exchange rate
- a derived "PPP FX Gap" must not be described as proof that the kwacha is overvalued or undervalued, and must not be presented as an exchange-rate forecast

Potential commands and views:
- `BENCHMARKS ZM`
- `PPP ZM`
- `REAL RATE ZM`
- `DEBT ZM`
- `EXTERNAL ZM`
- `MISERY ZM`

`BENCHMARKS ZM` should be the main entry point rather than creating one command for every ratio.

All benchmark outputs must identify whether they are:
- official observations
- ratios calculated from official observations
- derived analytical indicators

Methodology, source period and source vintage must be retained for calculated metrics. Correlation, valuation gaps and composite indices must not be presented as causal findings or official statistics.

#### Power and energy intelligence
Potential coverage:
- installed generation capacity
- actual generation
- electricity consumption and peak demand
- generation mix
- electricity imports and exports
- contracted versus delivered imports
- electricity tariffs
- power stations and energy infrastructure projects
- Kariba and other relevant water levels
- reservoir usable storage
- load-shedding and interruption data where reliable

Potential analytics:
- Hydropower Stress Index
- generation mix
- supply-demand trends
- regional power trade
- power generation versus mining activity

Keep installed capacity, actual generation, consumption, demand, contracted imports and delivered imports as separate measurements.

Existing ERB petroleum coverage forms part of this broader energy domain.

Energy Phase 1 (complete: backend and frontend): `docs/research/zambia-energy-data-sources.md` assesses the ERB Statistical Bulletin and Energy Sector Report as the strongest canonical-source candidates (annual only; SAPP, Ministry of Energy, ZESCO and the Zambia Statistics Agency not yet assessed in depth).

**Completed backend:** a tested, fail-closed parser for annual generation by station from the pinned ERB 2025 Statistical Bulletin Tables 13-16; vintage-aware canonical storage (migration `db/init/014_energy_generation_vintages.sql`, six tables and two views, designed in `docs/design/energy-generation-storage-vintages.md`, applied to the local database); limited application-role grants (migration 015: the API role cannot truncate, drop or alter Energy history); the storage importer service and manual import CLI; the controlled real import; and the stored-only read API (`/api/v1/energy/generation/latest`, `/history`, `/group-history`).

**Current real dataset:** ERB 2025 Annual Statistical Bulletin, 30 stations, 390 station/year observations, 52 published group totals, 2013-2025 (publication revision 1, import sequence 1). Rights remain `review_required`: local/internal use only.

**Completed frontend:** the `POWER ZM` terminal view (aliases `ENERGY ZM`, `GENERATION ZM`) with a dashboard card, latest publication/year overview, published group totals, station table, station and group annual history charts, provenance, a local/internal rights notice and a responsive layout. Energy Phase 1 is complete end to end. **Completed vertical: Commercial Bank USD/ZMW Rates (local/internal; rights `review_required`)**: source research (`docs/research/boz-commercial-bank-fx.md`), the strict v1/v2 parser and immutable storage (migration 017), the manual exact-date importer, two controlled real imports, the stored-only latest/history API and the `BANK FX` / `FX BANKS` frontend (dashboard card, exact stored-date selector, source slot selector, source-order bank table, BoZ-published market average, provenance and rights display) are done. The core local/internal vertical is complete end to end; public deployment or reuse remains blocked by the rights review. Derived Bank FX analytics are not implemented and are not scheduled here.

**Later Energy Phase 2 (not required for the first frontend):** installed generation capacity; electricity consumption; peak demand (annual-peak and all-time-peak definitions differ, so canonicalization is deferred); electricity imports and exports; contracted versus delivered imports; generation-mix analytics; power-generation/mining analytics; a reliable load-shedding history; water/reservoir data; power infrastructure and projects; an official source-replacement workflow; a reviewed local import-correction workflow; and public-rights approval.

#### Mining and mineral economy
Potential coverage:
- copper
- cobalt
- gold
- silver
- nickel
- zinc
- manganese
- iron ore
- lithium
- graphite
- rare-earth minerals
- limestone
- cement
- steel

Distinguish international benchmark prices from Zambia production, exports, export value and mine/project information.

Potential analytics:
- copper price versus USD/ZMW
- mineral exports versus FX
- production trends
- mining versus power generation
- commodity concentration

Do not assume a usable benchmark exists for every mineral.

### Zambia economic structure

#### Population and demographics
Potential coverage:
- total population and growth
- density
- age and sex distribution
- household size
- urbanisation
- migration
- employment and unemployment
- labour-force participation
- education and literacy
- poverty and household expenditure
- access to electricity, water and sanitation

Potential geography:
- country
- province
- district
- constituency
- ward

Keep census observations, survey estimates and projections distinct.

#### Business and establishment intelligence
Potential coverage:
- operating economic establishments
- registered businesses
- new registrations
- closures where reliably reported
- industry and geographic distribution
- establishment size and employment
- formal and informal activity
- enterprise growth and industry concentration

Keep registered legal entities distinct from operating economic establishments.

Potential analytics:
- businesses per 1,000 residents
- employment intensity
- regional economic diversification
- population-to-business ratios
- enterprise growth
- market catchment analysis

Potential sources include Zambia Statistics Agency and PACRA.

#### Insurance and financial-sector statistics
Potential aggregate coverage:
- gross written premiums
- claims and policyholder benefits
- industry assets
- insurance penetration and density
- product categories
- long-term versus general insurance
- pensions
- financial inclusion indicators

Where published, keep policy counts, policyholder counts and insured lives separate.

This remains an economic-statistics module, not an insurance administration system.

### Policy and regulatory intelligence

Potential coverage:
- government policies, strategies and frameworks
- bills and Acts of Parliament
- statutory instruments and regulations
- amendments
- budgets and fiscal measures
- executive decisions
- national development plans
- public programmes
- public consultations
- implementation milestones

Potential lifecycle:
`proposed -> introduced -> approved/enacted -> published -> effective/commenced -> amended -> repealed`

Potential features:
- Government Policy Timeline
- Legislative Activity Dashboard
- Regulatory Change Monitor
- Policy Document Search
- Policy Implementation Tracker
- Budget-Policy Explorer
- Policy-Economic Indicator Explorer

Policy counts are descriptive measures of documented activity and must not be presented as measures of government performance.

### Research and analytical capabilities

#### Deterministic analytics
Potential additions:
- percentage changes
- rolling averages
- spreads and ratios
- seasonality
- correlations
- lagged comparisons
- market divergence
- data freshness

Potential features:
- Market Pulse
- Data Freshness Radar
- Commodity Seasonality Explorer
- Copper-Kwacha Relationship Explorer
- Market Divergence Monitor
- Economic Calendar
- Market Event Timeline
- Cross-Market Correlation Explorer

Correlation must not be presented as evidence of causation.

#### Advanced and experimental analytics
Possible later capabilities:
- anomaly detection
- regime analysis
- forecasting experiments
- backtesting
- research notebooks
- saved research views
- source-aware summaries
- source-linked document search

Experimental forecasts must be clearly labelled as model outputs rather than official observations.

### ZM Terminal Atlas

A future geographic interface may complement the terminal once several datasets share stable geographic identifiers.

Potential layers:
- population
- business establishments
- agriculture
- mining
- energy infrastructure
- employment
- public infrastructure
- economic indicators

Potential capabilities:
- province and district profiles
- constituency and ward views where supported
- thematic maps
- geographic comparisons
- historical geographic trends
- regional economic dashboards
- infrastructure-access comparisons

Atlas should be a presentation and analysis layer over existing domain datasets, not a parallel ingestion architecture.

Administrative boundaries and boundary changes should be versioned where necessary.

### Data-constrained research areas

#### Real estate and property
Potential future coverage:
- housing stock
- land statistics
- construction activity and costs
- mortgage indicators
- asking prices and rents
- verified transactions
- rental yields
- vacancy rates

Always distinguish asking prices, transaction prices, estimates and derived indicators.

Do not create a national property-price series from scraped listings without a defensible methodology.

#### Broader agriculture
Potential future coverage includes:
- grains: wheat, rice, sorghum, millet, barley
- oilseeds and legumes: sunflower, groundnuts, mixed beans, cowpeas
- root crops: cassava, sweet potatoes, Irish potatoes
- cash crops: cotton, tobacco, sugarcane, coffee
- livestock and fisheries: cattle, goats, pigs, poultry, beef, milk, eggs, tilapia, kapenta
- agricultural inputs: fertilizer, urea and seed

These form a coverage universe, not an implementation backlog. Each commodity requires its own source verification.

Preserve distinctions between national-average, spot, procurement and government-sale prices.

### Source and ingestion capabilities

Document extraction is infrastructure, not a standalone product domain.

Adapters may use:
- native PDF text extraction
- structured table extraction
- OCR for image-only official documents
- image preprocessing where necessary
- document metadata extraction

OCR and computer vision are implementation details of source adapters, not independent ZM Terminal product features.

Any extracted observation must still pass source-specific validation before becoming authoritative.

### Long-term sequencing

These phases do not override the existing `Next` or `Planned` sections.

#### Phase A - current market-terminal roadmap
Complete the remaining planned items: gold (source discovery done, licence confirmation pending), copper and `COMPARE` / `CHART` / `CORR` (agriculture, global oil, `OIL ZM` and the sovereign yield curve are done).

#### Phase B - Zambia economic core
Prioritise:
1. macroeconomic expansion
2. power and energy intelligence
3. mining production and export intelligence

#### Phase C - economic structure
Then investigate:
1. population and demographics
2. economic establishments
3. PACRA registration statistics
4. insurance and pension statistics

#### Phase D - research intelligence
Then consider:
- policy and legislative intelligence
- economic calendar
- event timelines
- source-aware document search

#### Phase E - geographic intelligence
Build ZM Terminal Atlas once multiple mature datasets share stable geographic identifiers.

#### Phase F - experimental and data-constrained work
Only after stronger source foundations:
- real estate analytics
- forecasting
- anomaly detection
- backtesting
- broader AI-assisted research

## Long-term data principles

As ZM Terminal expands:
- do not force fundamentally different datasets into one schema
- preserve provider-specific adapters
- preserve source values before normalization
- retain source URLs and provenance
- distinguish publication, observation, effective and retrieval timestamps
- model historical revisions explicitly
- distinguish official, derived and experimental values
- preserve geographic granularity
- preserve source units
- use Decimal for financial values
- do not fabricate missing observations
- do not silently interpolate unless a derived series explicitly requires it
- verify licensing and reuse before ingestion
- prefer authoritative Zambia sources
- use international sources where they provide appropriate global benchmarks
- defer a feature when the source is unsuitable rather than weakening source standards

No item in this long-term section is an implementation commitment until separately researched and approved.
