# ZM Terminal

A personal, Zambia-first market intelligence terminal built with React, FastAPI, PostgreSQL, OpenBB and Docker.

> Status: **v0.1 scaffold with a Bank of Zambia USD/ZMW backend adapter**. The application deliberately does not display fabricated market prices. Frontend FX integration and LuSE, crypto and commodity adapters remain pending.

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
```

### Bank of Zambia USD/ZMW backend

`GET /api/v1/fx/USDZMW` fetches the official [BoZ USD observations JSON](https://www.boz.zm/api/v1/views/boz_zmw_usd_daily_exchange_rates).
`BOZ_USD_RATES_URL` configures that request URL; attribution remains the public [BoZ exchange-rate page](https://www.boz.zm/markets-securities/boz-exchange-rates).
The source labels the pair ZMW/USD; the application symbol `USDZMW` represents ZMW per USD without inversion.

The response preserves published `buy`, `mid`, and `sell` as **JSON decimal strings**. It extracts explicit UTC observation timestamps from each row's `<time datetime>` attribute, preserving seconds, and records a separate UTC `retrieved_at`.
It selects the latest timestamp across all hours, regardless of response order. The website applies an additional hour filter whose semantics are undocumented; this adapter does not reproduce it.
Identical duplicates are accepted. Conflicting duplicates at any timestamp, malformed rows, missing values, and future observations fail explicitly rather than silently selecting older data.

`stale` means the observation is **more than 96 hours old** at retrieval. This is an application heuristic that tolerates weekends and some holiday gaps, not a BoZ publication SLA or holiday calendar. Stale quotes still return HTTP 200 with `stale: true`.
Requests use a 10-second HTTPX timeout and a ZM Terminal User-Agent, without retries. Timeouts return 504; upstream HTTP failures or invalid data return a safe 502 response.

No quotes are persisted or cached. Frontend FX commands, quote display, and historical charts remain pending.
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
