# ZM Terminal

A personal, Zambia-first market intelligence terminal built with React, FastAPI, PostgreSQL, OpenBB and Docker.

> Status: **v0.1 with one live vertical slice: Bank of Zambia USD/ZMW** (adapter, PostgreSQL persistence, history API, live quote, detail view, chart, and `FX USDZMW` / `USDZMW` commands). The application deliberately does not display fabricated market prices. LuSE, crypto, commodity and other adapters remain pending.

## Goals

ZM Terminal is intended to make Zambia-specific financial research convenient alongside global market context.

Initial coverage:

- ZMW foreign exchange, beginning with USD/ZMW
- Lusaka Securities Exchange instruments and LASI
- Zambian Treasury bills and bonds
- Bitcoin in USD and ZMW
- Copper and selected global commodities
- Zambia macro indicators
- Watchlists, historical charts and a keyboard-first command palette

## Stack

- **Web:** React + TypeScript + Vite
- **Application API:** FastAPI
- **Database:** PostgreSQL
- **Financial data engine:** OpenBB Platform V5 REST API
- **Local runtime:** Docker Compose

OpenBB is kept behind the application API. The browser talks to FastAPI, allowing Zambian data sources and derived instruments to be combined with OpenBB without coupling the UI to a single provider.

## Architecture

```text
Browser
   |
   v
React / Vite :5173
   |
   v
FastAPI :8000
   |----------------------|
   v                      v
PostgreSQL :5432       OpenBB :6900
                          |
                   provider packages
```

## Quick start

Requirements:

- Docker Engine or Docker Desktop
- Docker Compose v2
- Git

```bash
git clone https://github.com/ElijahMwambazi/zm-terminal.git
cd zm-terminal
cp .env.example .env
docker compose up --build
```

Open:

- Web: http://localhost:5173
- ZM Terminal API: http://localhost:8000/docs
- OpenBB API: http://localhost:6900/docs

The FRED provider is installed but optional. Add a `FRED_API_KEY` to `.env` when you want FRED endpoints that require authentication.

## Useful commands

```bash
make up
make down
make logs
make ps
make api-test
make web-build
make clean
```

## API scaffold

```text
GET /api/v1/health
GET /api/v1/system
GET /api/v1/instruments
GET /api/v1/markets/overview
GET /api/v1/openbb/coverage
GET /api/v1/fx/USDZMW
GET /api/v1/fx/USDZMW/history?range=1D|1W|1M|3M|1Y|ALL   (default 1M)
GET /api/v1/providers
```

### Bank of Zambia USD/ZMW backend

`GET /api/v1/fx/USDZMW` fetches the official [BoZ USD observations JSON](https://www.boz.zm/api/v1/views/boz_zmw_usd_daily_exchange_rates).
`BOZ_USD_RATES_URL` configures that request URL; attribution remains the public [BoZ exchange-rate page](https://www.boz.zm/markets-securities/boz-exchange-rates).
The source labels the pair ZMW/USD; the application symbol `USDZMW` represents ZMW per USD without inversion.

The response preserves published `buy`, `mid`, and `sell` as **JSON decimal strings**. It extracts explicit UTC observation timestamps from each row's `<time datetime>` attribute, preserving seconds, and records a separate UTC `retrieved_at`.
It validates every row, then selects the latest timestamp **among rows in an official slot**, regardless of response order (see *Source-selection policy* below). A valid row outside the slots is *not selected*, not invalid; if no row is in a slot the request fails closed with a safe 502.
Identical duplicates are accepted. Conflicting duplicates at any timestamp, malformed rows, missing values, and future observations fail explicitly rather than silently selecting older data.

`stale` means the observation is **more than 96 hours old** at retrieval. This is an application heuristic that tolerates weekends and some holiday gaps, not a BoZ publication SLA or holiday calendar. Stale quotes still return HTTP 200 with `stale: true`.
Requests use a 10-second HTTPX timeout and a ZM Terminal User-Agent, without retries. Timeouts return 504; upstream HTTP failures or invalid data return a safe 502 response.

Each successful quote is persisted (exact `NUMERIC` values, `observed_at`, `retrieved_at`, `source`, `source_url`) to `fx_rates`, idempotently on `(pair, observed_at, source)`. Raw BoZ responses are never stored. If persistence fails the live quote is still returned and a generic warning (exception type only) is logged. Nothing is cached.

`GET /api/v1/fx/USDZMW/history` returns stored observations ascending by `observed_at` as `{symbol, range, count, observations[{observed_at, buy, mid, sell, source}]}` with decimal strings. Invalid ranges return 422; a database failure returns a safe 503. **History only contains observations this app has retrieved and stored**; it is not backfilled from BoZ, so it starts empty/sparse on a new database.

`GET /api/v1/providers` exposes the provider policy (`app/services/provider_policy.py`): public market data, no personal data, no authentication, normalized storage allowed, raw storage disabled, attribution required, redistribution and commercial use *review required*, and request guidance (one request per user action, no background polling).

**Existing databases:** `db/init` scripts only run on a fresh volume. Apply the provenance columns with
`docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/002_fx_rates_provenance.sql`.
After adding the `lightweight-charts` dependency, recreate the web `node_modules` volume (`docker compose down && docker volume rm zm-terminal_web_node_modules`).

### Historical backfill (manual job)

```bash
docker compose exec api python -m app.jobs.backfill_usdzmw --from 2026-01-22 [--to YYYY-MM-DD] [--dry-run]
# or, locally: cd apps/api && python -m app.jobs.backfill_usdzmw --from ...
```

Source: the same official BoZ JSON view as the live adapter (`boz_zmw_usd_daily_exchange_rates`), filtered with the `time[min]` / `time[max]` parameters that the BoZ website's own series chart uses. Verified behaviour (2026-11-12): same row shape and explicit UTC `<time datetime>` values, **about three observations per business day** (07:30, 10:30, 13:30 UTC = 09:30/12:30/15:30 Africa/Lusaka), newest first, a hard cap of **500 rows per request with no paging**, and **no rows before 2026-01-22**. Because the granularity matches the live model, no schema or API change was needed.

The job splits the range into sequential windows of at most 31 days (2 s apart, 10 s timeout, no retries, at most 400 days per run), fails if any window reaches the 500-row cap, validates every row with the live adapter's rules, and only then writes **one transaction**. Inserts are idempotent (`ON CONFLICT DO NOTHING`); an existing row with *different* values aborts the whole batch and is never overwritten. Valid rows outside the official slots are **not selected**: they are reported on stderr (`not_selected` count) and neither stored nor altered. They are not called invalid; invalid rows (malformed, conflicting, future) still fail the run. No raw payload is stored; nothing is interpolated.

The BoZ "Download historical rates" XLSX (history from 2011) was inspected but **deliberately not used**: it is an analyst workbook with multiple sheets and pivot tables, Excel floating-point values, a `#VALUE!` column, pre-rebasing kwacha values (~4,949), mixed interbank/retail series and timestamps without a timezone, so a safe exact-decimal UTC import cannot be derived from it without inference.

### Source-selection policy

`OFFICIAL_SLOT_UTC_HOURS = {7, 10, 13}` in `app/services/boz.py`, applied identically by the live adapter and the backfill. Evidence (BoZ Angular client, inspected 2026-11-12): `STANDARD_UTC_HOURS = new Set([7,10,13])` is used by `mapLatestUsdRates`, which picks the site's *latest* USD rates by `getUTCHours()` only (09:30/12:30/15:30 Africa/Lusaka), falling back to all rows if none match. The site's *series* function (`getBankOfZambiaDollarExchangeRatesSeries`) applies **no** hour filter, so the rule is evidenced for BoZ's headline rates, not for its series; applying it to history is this project's choice, made so an off-slot row cannot appear live yet be absent from history (or vice versa). Hour is compared alone (as on the site), not minutes. Unlike the site there is **no fallback** to other hours. Hour is not treated as proof a row is mathematically wrong; no statistical outlier thresholds are used. Off-slot rows seen in 2026 (e.g. a 2.7486 mid among ~18.6 values on 2026-07-27 08:00Z) are therefore excluded by policy, not diagnosed.

### Frontend

The overview shows a live USD/ZMW card (mid, buy/sell, BoZ, Africa/Lusaka observation time, fresh/stale) with loading, API-unavailable and BoZ-unavailable states; no placeholder prices are substituted. `FX USDZMW` or `USDZMW` (Ctrl+K focuses the input; case-insensitive, whitespace tolerant) opens the detail view: quote, provenance, observed/retrieved times, stale state, a refresh button (one BoZ request per click), range selector and a [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) line of `mid`. It shows empty, loading, error and "insufficient history" (one point) states. Unknown commands show a local error.

Backend tests: `cd apps/api && python -m pytest`. PostgreSQL integration tests run when `TEST_DATABASE_URL` points to a disposable database whose name ends in `_test` (CI provides one); otherwise they are skipped.
Offline tests use a small sanitized JSON fixture with altered test values, a preserved upstream structure, and mocked HTTP; they require neither BoZ access, PostgreSQL, nor OpenBB. Run `cd apps/api && python -m pytest` (or the existing Docker-based `make api-test`).

## Planned terminal commands

```text
FX USDZMW
LU CECZ
LU ZNCO
BTC
BTCZMW
COPPER
TBILL ZM
BOND ZM
MACRO ZM
WATCH
CHART USDZMW 1Y
COMPARE BTC COPPER USDZMW
```

## Data-provider strategy

The application will normalize providers behind its own service layer:

```text
USD/ZMW -> Bank of Zambia adapter
LuSE    -> LuSE adapter
Global  -> OpenBB provider packages
BTC/ZMW -> derived from BTC/USD and USD/ZMW
```

This repository does not assume that OpenBB provides Zambia-specific market data.

## Project layout

```text
zm-terminal/
├── apps/
│   ├── api/               # FastAPI application
│   └── web/               # React/Vite terminal UI
├── db/
│   └── init/              # PostgreSQL bootstrap schema
├── docker/
│   └── openbb/            # OpenBB V5 API image
├── .github/workflows/     # CI
├── compose.yml
├── .env.example
├── Makefile
└── README.md
```

## Roadmap

### v0.1 — Foundation

- [x] Docker Compose development environment
- [x] React terminal shell
- [x] FastAPI application API
- [x] PostgreSQL schema
- [x] OpenBB V5 API service
- [x] CI scaffold
- [x] Bank of Zambia USD/ZMW backend adapter
- [ ] Frontend FX integration
- [ ] LuSE market-data adapter
- [ ] BTC/USD provider
- [ ] BTC/ZMW derived instrument
- [ ] Copper data provider
- [ ] Historical charting
- [ ] Command parser
- [ ] Watchlist persistence

### v0.2 — Research

- Yield curve views
- Instrument comparisons
- Correlation tools
- Zambia/global macro overlays
- Historical data ingestion jobs

### v0.3 — Intelligence

- Research queries over structured market data
- Source-aware explanations
- Alerts
- Optional agent/MCP workflows

## Development principles

1. Never display fabricated financial prices.
2. Preserve the source and observation time for market data.
3. Keep Zambia-specific providers first-class rather than forcing them through OpenBB.
4. Prefer a small local-first architecture until complexity is justified.
5. Keep provider-specific details out of the frontend.

## License

MIT
