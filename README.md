# ZM Terminal

A personal, Zambia-first market intelligence terminal built with React, FastAPI, PostgreSQL, OpenBB and Docker.

> Status: **active Zambia-first market terminal with multiple implemented vertical slices**: Bank of Zambia USD/ZMW, Treasury bills, Government bonds, stored fixed-income history, ERB fuel prices/build-ups, Ministry of Agriculture maize/soya national-average prices, global Brent/WTI monthly oil benchmarks (World Bank Pink Sheet), the derived `OIL ZM` monthly Brent-in-kwacha indicator, the derived Zambia sovereign auction-yield tenor curve, Bitstamp BTC/USD with an indicative derived BTC/ZMW, and the IMF Primary Commodity Prices global Copper monthly benchmark with the derived `COPPER ZM` monthly Copper-in-kwacha indicator (BTC and Copper are local/internal use; public rights pending). The application deliberately does not display fabricated market prices. LuSE, broader macro and other planned domains remain pending.

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

## Implemented API

```text
GET /api/v1/health
GET /api/v1/system
GET /api/v1/instruments
GET /api/v1/markets/overview
GET /api/v1/openbb/coverage
GET /api/v1/providers

GET /api/v1/fx/USDZMW
GET /api/v1/fx/USDZMW/history?range=1D|1W|1M|3M|1Y|ALL
GET /api/v1/fx/USDZMW/monthly?range=1Y|3Y|5Y|ALL

GET /api/v1/treasury-bills/latest
GET /api/v1/treasury-bills/{91D|182D|273D|364D}
GET /api/v1/treasury-bills/history?tenor=...&range=1M|3M|6M|1Y|ALL

GET /api/v1/government-bonds/latest
GET /api/v1/government-bonds/{2Y|3Y|5Y|7Y|10Y|15Y}
GET /api/v1/government-bonds/history?tenor=...&range=3M|6M|1Y|ALL

GET /api/v1/fuel/latest
GET /api/v1/fuel/{PETROL|DIESEL|KEROSENE|JET_A1|LSGO}
GET /api/v1/fuel/history?product=...&range=1Y|3Y|5Y|ALL&type=retail|wholesale
GET /api/v1/fuel/build-up?product=...&type=pump|wholesale&month=YYYY-MM

GET /api/v1/agriculture/latest
GET /api/v1/agriculture/{MAIZE|SOYA_BEANS}
GET /api/v1/agriculture/history?commodity=...&range=1Y|3Y|ALL
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

Each successful quote is persisted (exact `NUMERIC` values, `observed_at`, `retrieved_at`, `source`, `source_url`) to `fx_rates`, idempotently on `(pair, observed_at, source)` using the same conflict-aware logic as the backfill: a new row is inserted, identical values (numeric comparison, `19.8 == 19.8000`) are a silent no-op, and different values for the same key are **never overwritten**. Raw BoZ responses are never stored. If persistence fails or conflicts, the live quote is still returned and only a generic warning is logged (`USDZMW persistence conflict; live quote returned unsaved`, or the exception type). Nothing is cached.

`GET /api/v1/fx/USDZMW/history` returns stored observations ascending by `observed_at` as `{symbol, range, count, observations[{observed_at, buy, mid, sell, source}]}` with decimal strings. Invalid ranges return 422; a database failure returns a safe 503. History can include both live observations saved by the app and rows imported by the manual BoZ backfill job; the verified BoZ JSON history begins on 2026-01-22.

`GET /api/v1/providers` exposes the provider policy (`app/services/provider_policy.py`): public market data, no personal data, no authentication, normalized storage allowed, raw storage disabled, attribution required, redistribution and commercial use *review required*, and request guidance (one request per user action, no background polling).

**Existing databases:** `db/init` scripts only run on a fresh volume. Apply the provenance columns with
`docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/002_fx_rates_provenance.sql`.
After adding the `lightweight-charts` dependency, recreate the web `node_modules` volume (`docker compose down && docker volume rm zm-terminal_web_node_modules`).

### Official BoZ monthly USD/ZMW averages (phase 1, local/internal)

A **separate dataset** from the intraday history above: official Bank of Zambia **monthly average** buy / sell / **published mid** rates (ZMW per USD), from Table 02 `KWACHA/US DOLLAR EXCHANGE RATES`, "Monthly Averages" (Bank of Zambia Rates columns) of the BoZ **Fortnightly Statistics** PDFs. Phase-1 coverage is **2014-01 to 2026-09** (153 months, rebased kwacha, 2 published decimals). It lives in its own table `fx_monthly_averages` (migration `012_fx_monthly_averages.sql`), never in `fx_rates`: a row is a calendar month, has no `observed_at`, and the API shows `month` as `YYYY-MM`. The published mid is stored exactly; it is never computed from buy and sell. BoZ's monthly-average methodology is not documented, so the existing observed-days method (used by `OIL ZM` and `COPPER ZM`) is a different series and nothing in those views changed.

```bash
docker compose exec api python -m app.jobs.backfill_fx_monthly [--from YYYY-MM] [--to YYYY-MM] [--dry-run]
# or, locally: cd apps/api && python -m app.jobs.backfill_fx_monthly --dry-run
```

Coverage is **2010-01 to 2026-09** (201 months): 2010-2013 from the BoZ Annual Reports (Table 13, 48 months, published old kwacha divided exactly by 1000 before 2013) and 2014 onward from the Fortnightly Statistics (153 months). The source family is chosen from the requested months; the 2014 Annual Report is a validation bridge only. Apply migration 013 before a real run (the importer never migrates; `--dry-run` needs no database). The 2010-2013 rows are implemented and tested but **not yet imported** into any normal database.

The importer requests only vetted publications: the 13 Fortnightly ones in `app/services/boz_monthly_source.py` and the five Annual Reports in `app/services/boz_annual_source.py` (derived from `docs/research/boz-deeper-fx-history.md` section 6.1; no URL is guessed and the archive is not crawled): one sequential GET each, 2 s apart, no retries, HTTPS `www.boz.zm` only, no redirects, with a size cap per family (Fortnightly PDFs 5 MB, Annual Reports 20 MB). **Fortnightly path:** it extracts the PDF text layer (no OCR) and requires exactly one `Table 02` / `KWACHA/US DOLLAR EXCHANGE RATES` / `Monthly Averages` block with the BoZ columns first and consecutive months under explicit year labels, enforces `buy <= mid <= sell` (the named exception list is empty in phase 1) and does **not** treat a mid that differs from `(buy + sell) / 2` as invalid (2019-03 and 2022-11 are published that way). Every overlapping month across publications must be numerically identical or the whole import fails; the stored row's provenance is the **earliest** vetted publication containing that month. **Annual Report path:** each PDF is pinned by SHA-256, size and page, one page is extracted with Poppler `pdftotext -layout` and parsed as `Table 13` (see the Phase 2 notes below), with old kwacha divided exactly by 1000 before 2013. Missing months fail the import (nothing is interpolated). Persistence is one atomic conflict-aware transaction (new = insert, identical = already present, different = conflict, nothing written); `--dry-run` never touches PostgreSQL. Output is counts and months only. A full 2010-2026 run makes 18 vetted fetches (13 Fortnightly, 4 canonical Annual Reports and the validation-only 2014 Annual Report) and takes about 3 minutes including PDF text extraction; bounded ranges fetch only what they need.

`GET /api/v1/fx/USDZMW/monthly?range=1Y|3Y|5Y|ALL` (default `ALL`; invalid range `422`; database failure a safe `503`) reads PostgreSQL only and never contacts BoZ or parses a PDF. Ranges are **counts of stored months** (`1Y` = the latest 12, `3Y` = 36, `5Y` = 60, `ALL` = all), returned ascending as `{symbol, range, count, first_month, last_month, observations[{month, buy, mid, sell, source, source_series, source_publication, source_url, source_page, published_precision}]}` with decimal strings. There is no stale flag and no timestamp.

Apply the migration to an existing database volume (idempotent; it only creates the new table): `docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/012_fx_monthly_averages.sql`. **Phase 2 (implemented; the 2010-2013 rows are not yet applied to the normal database):** migration `db/init/013_fx_monthly_published_values.sql` (apply after 012, in one transaction, idempotent: `docker compose exec -T postgres psql -U zm_terminal -d zm_terminal -v ON_ERROR_STOP=1 --single-transaction < db/init/013_fx_monthly_published_values.sql`) keeps the existing canonical `buy`/`mid`/`sell` and adds the exact printed values, printed currency (`ZMK` before 2013-01, `ZMW` after) and the divisor (1000 or 1), with constraints that enforce `canonical * divisor = printed`; existing rows are migrated in place. The BoZ Annual Report extraction layer (`pdftotext -layout` on one pinned page, Table 13 parser, month-based exact ÷1,000 normalization, the two named ordering exceptions 2012-04 and 2013-12, and the 2014 validation bridge) is implemented and tested, and the combined importer above (2010-01 to 2026-09, expected 201 months: 48 Annual Report plus 153 Fortnightly observations) is implemented. The monthly API read path serves both approved source series (one row per month, failing closed on a duplicate month; the response contract is unchanged), so `ALL` returns 201 months once the rows exist. **Migration 013 and the 48 historical rows have not been applied to the normal database**; both remain a controlled manual step (apply 013 first, then run the importer). **System dependency:** Poppler's `pdftotext` (the API Docker image installs `poppler-utils`; locally `apt install poppler-utils` / `brew install poppler`); the offline tests that need the real binary skip when it is absent, and `ZM_BOZ_ANNUAL_PDF_DIR` can point an optional test at cached vetted PDFs (never committed). **Not implemented:** applying the backfill to the normal database, a frontend change for it, `copper_zm_v2` and any Copper methodology change. **Rights are `review_required`: local/internal use only**; public redistribution and commercial use are not approved and Bank of Zambia has not been contacted.

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

Backend tests: `cd apps/api && python -m pytest`. PostgreSQL integration tests run when `TEST_DATABASE_URL` points to a disposable database whose name ends in `_test` (CI provides one); otherwise they are skipped. The Energy and role tests (`test_energy_vintage_schema.py`, `test_energy_schema_shadowing.py`, `test_application_role_privileges.py`) use a stricter fail-closed guard (`tests/disposable_db.py`): only `postgresql+psycopg` URLs, no query parameters at all, a non-empty database name ending in `_test` and never `zm_terminal`, all checked on the parsed URL before any engine exists; they run in a database created for the session (dropped at the end) whose `public` schema is reset before every test. **The older PostgreSQL test files still use the raw name-suffix check and have not been migrated to it yet**, so point `TEST_DATABASE_URL` only at a database you are happy to lose.

**Database roles (migrations 015-016).** `POSTGRES_USER` (`zm_terminal`) is the admin/owner role: it runs migrations, owns every table, view, sequence and function, and is the only role that can `TRUNCATE`, drop or alter. The API connects as the application role (`APP_DB_USER`, default `zm_terminal_app`; password `APP_DB_PASSWORD`), which owns nothing, belongs to no other role, cannot `CREATE` in the schema or database, and has only `SELECT/INSERT/UPDATE` on the legacy tables and, on the Energy tables, `SELECT`/`INSERT` plus `UPDATE` of lifecycle-status columns only (no `DELETE`, `TRUNCATE` or DDL). `APP_DB_USER`/`APP_DB_PASSWORD` are the single source of truth: Compose gives the same values to the Postgres container (which creates the role and sets its password) and to the API container (which builds its connection URL from them; no password default lives in the API code). The API never receives the admin password and refuses to start a connection as the admin role. `DATABASE_URL` remains an optional override that wins over `APP_DB_*` when set, but it must name the application role: a `DATABASE_URL` that names the admin role (for example one copied from an older `.env.example`) is rejected, so remove it from `.env` and `apps/api/.env`. A fresh volume creates everything automatically (`db/init/015_application_role.sql`, `016_application_role_password.sh`). 015 converges rather than only adds: it re-establishes the role's attributes explicitly (including `LOGIN`, so an old `NOLOGIN` role becomes usable), revokes every role membership, clears every stored role setting (`ALTER ROLE … RESET ALL`, globally and `IN DATABASE` for the current database, so a leftover `session_replication_role = replica` cannot disable the triggers at login; the same role's settings in other databases and other roles' settings are left alone), revokes schema/database `CREATE`, and fails closed if the role owns anything or can still create in the schema (for example through a `PUBLIC` grant on a pre-PG15 `public` schema); it is idempotent, so re-run it after applying any later migration (for example 014) so the new objects are granted.

**Rollout for an existing volume** (init scripts do not re-run; the data volume is kept throughout). Supply the password without putting it on a command line or in the shell history, for example `read -rs APP_DB_PASSWORD && export APP_DB_PASSWORD` (not echoed; whether it reaches history depends on how you supply it, and a `.env` file should be mode 600), then:

```
docker compose up -d --no-deps postgres        # recreate postgres so it receives APP_DB_USER / APP_DB_PASSWORD / PGOPTIONS
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 --single-transaction' < db/init/015_application_role.sql
docker compose exec -T postgres sh /docker-entrypoint-initdb.d/016_application_role_password.sh
docker compose up -d --no-deps --force-recreate api   # same APP_DB_* credentials; unset any admin DATABASE_URL first
```

`APP_DB_PASSWORD` stays in the container environment and 016 lets psql read it itself with `\getenv`, so it is never a psql command-line argument, never part of the script or SQL text, and is quoted by psql (`:'pw'`), which also makes special characters safe; it is the same `APP_DB_PASSWORD` the API container receives. Do not rely on the Postgres socket healthcheck: verify that the application role can make a TCP-authenticated connection, that it is the role you expect, and that destructive rights are absent (non-destructive checks; expected output in comments):

```
docker compose exec -T postgres sh -c 'PGPASSWORD="$APP_DB_PASSWORD" psql -h 127.0.0.1 -U "$APP_DB_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v admin="$POSTGRES_USER" -At' <<'SQL'
SELECT current_user, current_database();                                  -- the application role and your database
SELECT count(*) >= 0 AS select_ok FROM fx_rates;                          -- t
SHOW session_replication_role;                                            -- origin (no stored role setting changes it)
SELECT has_table_privilege(current_user, 'fx_rates', 'TRUNCATE');         -- f
SELECT pg_has_role(current_user, :'admin', 'USAGE');                      -- f: the admin role is unreachable (SET ROLE would fail)
SQL
docker compose exec -T api python -c "from sqlalchemy import text; from app.db import get_engine; print(get_engine().connect().execute(text('SELECT current_user, current_database()')).one())"
```
Offline tests use a small sanitized JSON fixture with altered test values, a preserved upstream structure, and mocked HTTP; they require neither BoZ access, PostgreSQL, nor OpenBB. Run `cd apps/api && python -m pytest` (or the existing Docker-based `make api-test`).

## Implemented terminal commands

```text
FX USDZMW
USDZMW
TBILL ZM
TBILL 91D
TBILL 182D
TBILL 273D
TBILL 364D
BOND ZM
BOND 2Y
BOND 3Y
BOND 5Y
BOND 7Y
BOND 10Y
BOND 15Y
FUEL ZM
FUEL PETROL
FUEL DIESEL
FUEL KEROSENE
FUEL JET
FUEL LSGO
AGRI ZM
MAIZE ZM
SOYA ZM
MAIZE AVG
SOYA AVG
OIL
OIL BRENT
OIL WTI
OIL ZM
CURVE ZM
YIELD CURVE ZM
BTC
BTC USD
BTC ZMW
COPPER
COPPER ZM
COPPER FX
COPPER KWACHA
```

`OIL ZM` is a derived monthly indicator (World Bank Brent monthly average x the mean of stored daily BoZ USD/ZMW mid-rate averages over the stored observation days, not a full-month FX average), computed from stored data only and labelled DERIVED; it is not an official series or a pump price. `CURVE ZM` / `YIELD CURVE ZM` open a derived Zambia sovereign auction-yield tenor curve: the latest stored accepted BoZ auction yield for each published T-bill and bond tenor at or before an as-of date, with mixed auction vintages shown; it is not an official BoZ curve and not a bootstrapped or fitted curve, and bond tenors are published buckets, not remaining maturities. The same view shows four derived published-tenor spreads (10Y-364D, 15Y-364D, 10Y-2Y, 15Y-91D: long yield minus short yield in basis points, computed by the API from the same curve snapshot, with each endpoint's auction date and a mixed-vintage flag, and no interpretation). Future command ideas such as BTC, copper, macro and cross-market research commands are tracked in `docs/ROADMAP.md`.

`BTC` / `BTC USD` open Bitstamp BTC/USD SPOT: the live last trade (one exchange's market, not a global reference price; each backend live endpoint performs at most one Bitstamp request per invocation, with no retries and no polling, and stores nothing; the history endpoint is stored-only) and a chart of stored completed daily OHLCV with range buttons that are rolling-day windows (1M = 30 days, 3M = 91, 6M = 182, 1Y = 365, 3Y = 1095, 5Y = 1825, ALL = everything stored; not calendar months or years) (`python -m app.jobs.backfill_btc`, manual; apply `db/init/010_btc_usd_daily.sql` to an existing database with `docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/010_btc_usd_daily.sql`). `BTC ZMW` opens an indicative derived BTC/ZMW: Bitstamp BTC/USD last x the latest stored BoZ USD/ZMW mid, with both inputs' ages shown; it is not an executable BTC/ZMW quote and never calls BoZ. The same view has a `DERIVED DAILY HISTORY` section (`GET /api/v1/btc/zmw/history`): each stored completed Bitstamp UTC-day close x the mean of that Africa/Lusaka date's stored BoZ USD/ZMW mids, computed on request from stored data only (nothing persisted, no upstream request); dates without a BoZ observation are omitted (no forward-fill or interpolation), the two inputs are not simultaneous, and the series can only start where the stored BoZ history begins. **This is a local/internal implementation:** public display, API redistribution and commercial use of Bitstamp data remain `review_required` pending Bitstamp's written clarification (see `docs/research/btc-provider-discovery.md`).

`COPPER` opens the **global Copper benchmark** from the IMF Primary Commodity Prices (PCPS, series `G001.PCOPP.USD.M`): one nominal-USD **monthly average** per calendar month in US dollars per metric ton, defined by the IMF as copper, grade A cathode, LME spot price, CIF European ports. It is a global benchmark, not a Zambia copper price, not a live quote and not a futures price, and the IMF (not LME) is the data provider. The API reads PostgreSQL only; history is imported manually with `docker compose exec api python -m app.jobs.backfill_copper --from 1992-01 [--to YYYY-MM] [--dry-run]` (one IMF request, no key, no raw storage; a changed stored month is a conflict, never an overwrite). Apply `db/init/011_global_copper_prices.sql` to an existing database with `docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/011_global_copper_prices.sql`. **This is a local/internal implementation:** public display, API redistribution, commercial use and any public derived use (including the derived `COPPER ZM` below) remain `review_required` pending the IMF's clarification on the underlying LME origin (`docs/research/copper-provider-discovery.md`; the IMF email is unsent).

`COPPER ZM` is a **derived ZM Terminal analytical indicator** (not an IMF, LME or Bank of Zambia series, not a Zambia copper price, not a mine-gate or export price, not live and not executable): `zmw_per_mt = IMF Copper USD/mt × usd_zmw_observed_days_avg`, methodology `copper_zm_v1`. The FX component follows the `OIL ZM` method over the same stored BoZ USD/ZMW population (the FX query and helpers are shared with `OIL ZM`; the aggregation is a separate implementation guarded by parity tests): observations are assigned to Africa/Lusaka calendar days, averaged within each day, and the month's value is the arithmetic mean of those daily averages, **each stored day weighted equally** (a day with more observations does not count more). Days without a stored observation are omitted, never filled or interpolated, and a month is derivable whenever one stored Copper month and at least one stored FX day exist in it (`fx_day_count` and `fx_observation_count` show the support); availability is the overlap of the two stored histories only, and the month containing the start of stored BoZ history is marked `partial_source_window`. The IMF monthly average and the BoZ observations follow different observation schedules within the month. Everything is computed on request from PostgreSQL (`GET /api/v1/copper/zm/latest`, `GET /api/v1/copper/zm/history?range=1Y|3Y|5Y|ALL`, default `1Y`); nothing derived is persisted, no migration is needed and neither the IMF nor BoZ is contacted. Local/internal only: public display, API redistribution, commercial use and public derived use remain `review_required`.

`COPPER FX` (alias `COPPER KWACHA`) opens the **Copper–Kwacha comparison**, a descriptive frontend view over the existing `COPPER ZM` history (no new API, data or persistence). It shows the same matched monthly observations for IMF Copper USD/mt, the BoZ USD/ZMW observed-days average and the derived COPPER ZM ZMW/mt, each **indexed to 100 at the first matched month of the selected range** (`index = value / first value × 100`, a visualization transform only and not an official index), plus a table of the actual values with BoZ day and observation counts. COPPER ZM is mechanically Copper USD × USD/ZMW, so the three series are not independent and visual co-movement is not evidence of causation. Only months with both stored inputs appear (nothing is filled); `1Y`, `3Y`, `5Y` and `ALL` currently show the same window because stored BoZ history begins in January 2026. The same view adds a **descriptive Pearson correlation** (`GET /api/v1/copper/kwacha/correlation`, methodology `copper_kwacha_corr_v1`) between month-over-month percentage changes in Copper USD and USD/ZMW (never raw levels, and never the derived COPPER ZMW), with a full-sample coefficient (at least 6 eligible changes, sample size always shown, a small-sample warning below 12) and rolling `6M`/`12M` windows, where a window means 6 or 12 consecutive eligible monthly changes and never crosses a missing month, the partial FX source-start month or a nonpositive input. It is calculated on request from stored data (nothing persisted) and does not establish causation or predictive power; no significance test, strength label or regression exists. The same view adds a **historical sensitivity (β)** (`GET /api/v1/copper/kwacha/sensitivity`, methodology `copper_kwacha_beta_v1`): the covariance of monthly USD/ZMW changes with monthly Copper USD changes divided by the variance of the Copper changes, on exactly the eligible sample the correlation uses (full sample only, at least 6 changes, a small-sample warning below 12, undefined when the Copper changes are constant). USD/ZMW is kwacha per US dollar, so a positive change is a depreciation of the kwacha against the dollar; β is descriptive, not a causal estimate, forecast or hedge ratio, and no intercept, fit, R², significance test or rolling β exists.

## Data-provider strategy

The application will normalize providers behind its own service layer:

```text
USD/ZMW -> Bank of Zambia adapter
LuSE    -> LuSE adapter
Global  -> OpenBB provider packages
BTC/USD -> Bitstamp public API adapter (direct; OpenBB is not involved)
BTC/ZMW -> derived from Bitstamp BTC/USD and stored BoZ USD/ZMW
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

The canonical sequencing lives in [`docs/ROADMAP.md`](docs/ROADMAP.md), while [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) records implemented behavior and known limitations.

Current implemented domains are USD/ZMW, BoZ Treasury bills and Government bonds with stored history, ERB fuel prices/build-ups, Ministry maize/soya national-average prices, and global Brent/WTI monthly oil benchmarks (World Bank Pink Sheet, USD/bbl, stored and imported manually with `python -m app.jobs.backfill_oil --from 2025-01`; see `docs/CURRENT_STATE.md`). The derived `OIL ZM` view (stored-only; available from January 2026, where BoZ history begins) is implemented. The derived Zambia sovereign auction-yield tenor curve is also implemented. The local/internal IMF Copper monthly benchmark, the derived `COPPER ZM` view and the descriptive `COPPER FX` comparison are implemented. The next planned item is gold provider discovery (licensing first).

## Development principles

1. Never display fabricated financial prices.
2. Preserve the source and observation time for market data.
3. Keep Zambia-specific providers first-class rather than forcing them through OpenBB.
4. Prefer a small local-first architecture until complexity is justified.
5. Keep provider-specific details out of the frontend.

## License

MIT
