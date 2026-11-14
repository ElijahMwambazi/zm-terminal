# ZM Terminal roadmap

Implemented work lives in `docs/CURRENT_STATE.md`; this file is only sequencing. Anything under
"Next" or "Planned" is **not implemented**.

## Completed
- BoZ USD/ZMW (live, stored history, manual backfill)
- Treasury Bills (live, stored history, manual backfill)
- Government Bonds (live, stored history, manual backfill)
- Fixed-income history (APIs, charts)
- ERB fuel price history (XLSX archive import, API, UI, `FUEL` commands)
- ERB latest-month retail prices + fuel build-ups (monthly PDF discovery, strict text parsing, narrow OCR fallback, stored build-up API/UI)
- Zambia agriculture source discovery (`docs/research/zambia-agriculture-sources.md`)
- Ministry maize + soya national-average vertical slice (Layout B, Apr 2025-Jan 2026, stored API/history/UI and `AGRI` commands)

## Current
- **Global oil: Brent + WTI** is implemented on `feat/global-oil-benchmarks` (OpenBB/FRED, stored history, API, UI, `OIL` commands) and verified offline. Live verification with a real `FRED_API_KEY` is outstanding (blocked: key missing); it moves to Completed only once the live response shape and a first backfill are confirmed.

## Next
- **`OIL ZM`** (Brent converted at the BoZ USD/ZMW rate): derived view, only after the stored Brent history and BoZ FX semantics are confirmed stable. Not implemented.

## Planned
- Zambia sovereign yield curve (needs the stored T-bill and bond history)
- Gold provider discovery (licensing first)
- `GOLD` / `GOLD ZM` once a gold source is approved
- BTC/USD + BTC/ZMW
- Copper
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
- Kwacha-Copper Relationship: rolling correlation or sensitivity analysis
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
Complete the already planned agriculture, global oil / `OIL ZM`, sovereign yield curve, gold source discovery, BTC, copper and `COMPARE` / `CHART` / `CORR`.

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
