# Current state

Implemented and verified on branch `feat/usdzmw-vertical-slice`:

- **BoZ USD/ZMW adapter** (`GET /api/v1/fx/USDZMW`): unchanged validation and error behaviour (504 timeout, safe 502, 96 h stale flag, no OpenBB dependency).
- **Persistence**: successful quotes stored in `fx_rates` (exact decimals, `retrieved_at`, `source_url`); idempotent; no raw payloads. Persistence failure does not fail the quote request.
- **History**: `GET /api/v1/fx/USDZMW/history?range=1D|1W|1M|3M|1Y|ALL`.
- **Provider policy**: `app/services/provider_policy.py`, exposed at `GET /api/v1/providers` (BoZ only).
- **Historical backfill** (`python -m app.jobs.backfill_usdzmw`): bounded, atomic, idempotent import of BoZ observations from 2026-01-22 using the official JSON view's date filter; conflicts abort without overwriting; valid off-slot rows are reported as not selected. Manual only (no scheduler or HTTP endpoint).
- **Web**: live USD/ZMW card, USD/ZMW detail view with provenance, range selector and Lightweight Charts `mid` line; command palette with `FX USDZMW` and `USDZMW` only.
- **CI**: API job runs a PostgreSQL service so the integration tests execute.

Not implemented: history before 2026-01-22 (the JSON view has none; the XLSX archive was not ingested, see README), LuSE, BTC, copper, T-bills, macro, watchlists (static placeholders), other commands, authentication, alerts, history backfill from BoZ, scheduled ingestion, frontend tests, a frontend lint step.

Known limitations:

- History is limited to what the JSON view serves (from 2026-01-22) plus quotes retrieved by this app. The filter's timezone is undocumented (harmless for whole-day windows given the 07:30-13:30 UTC slots).
- A few published rows are inconsistent and stored as published, e.g. buy > sell on 2026-03-19 07:30Z and 10:30Z; the live adapter and backfill share one source-selection policy (UTC hours 07/10/13, mirroring the BoZ site's latest-rate selection; its series chart is unfiltered, and unlike the site there is no fallback), so rows in those slots are trusted as published.
- A conflicting stored row is detected (safe warning) but not resolved: the live quote is returned unsaved and the stored row is kept; there is no reconciliation tool.
- Redistribution and commercial-use terms of BoZ data are unreviewed.
- Migrations are plain SQL in `db/init` (applied automatically only on a fresh database volume).
