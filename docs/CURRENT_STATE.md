# Current state

Implemented and verified on branch `feat/usdzmw-vertical-slice`:

- **BoZ USD/ZMW adapter** (`GET /api/v1/fx/USDZMW`): unchanged validation and error behaviour (504 timeout, safe 502, 96 h stale flag, no OpenBB dependency).
- **Persistence**: successful quotes stored in `fx_rates` (exact decimals, `retrieved_at`, `source_url`); idempotent; no raw payloads. Persistence failure does not fail the quote request.
- **History**: `GET /api/v1/fx/USDZMW/history?range=1D|1W|1M|3M|1Y|ALL`.
- **Provider policy**: `app/services/provider_policy.py`, exposed at `GET /api/v1/providers` (BoZ only).
- **Web**: live USD/ZMW card, USD/ZMW detail view with provenance, range selector and Lightweight Charts `mid` line; command palette with `FX USDZMW` and `USDZMW` only.
- **CI**: API job runs a PostgreSQL service so the integration tests execute.

Not implemented: LuSE, BTC, copper, T-bills, macro, watchlists (static placeholders), other commands, authentication, alerts, history backfill from BoZ, scheduled ingestion, frontend tests, a frontend lint step.

Known limitations:

- History only contains quotes retrieved through this app; it is sparse until the quote has been requested over time.
- Redistribution and commercial-use terms of BoZ data are unreviewed.
- Migrations are plain SQL in `db/init` (applied automatically only on a fresh database volume).
