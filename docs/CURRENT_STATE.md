# Current state

Implemented and verified on branch `feat/usdzmw-vertical-slice`:

- **BoZ USD/ZMW adapter** (`GET /api/v1/fx/USDZMW`): unchanged validation and error behaviour (504 timeout, safe 502, 96 h stale flag, no OpenBB dependency).
- **Persistence**: successful quotes stored in `fx_rates` (exact decimals, `retrieved_at`, `source_url`); idempotent; no raw payloads. Persistence failure does not fail the quote request.
- **History**: `GET /api/v1/fx/USDZMW/history?range=1D|1W|1M|3M|1Y|ALL`.
- **Provider policy**: `app/services/provider_policy.py`, exposed at `GET /api/v1/providers` (three BoZ datasets: USD/ZMW rates, Treasury Bill and Government Bond auction results).
- **Historical backfill** (`python -m app.jobs.backfill_usdzmw`): bounded, atomic, idempotent import of BoZ observations from 2026-01-22 using the official JSON view's date filter; conflicts abort without overwriting; valid off-slot rows are reported as not selected. Manual only (no scheduler or HTTP endpoint).
- **Web**: live USD/ZMW card, USD/ZMW detail view with provenance, range selector and Lightweight Charts `mid` line; command palette with `FX USDZMW` and `USDZMW` only.
- **CI**: API job runs a PostgreSQL service so the integration tests execute.

Not implemented: history before 2026-01-22 (the JSON view has none; the XLSX archive was not ingested, see README), LuSE, T-bill history/PDF-only fields, BTC, copper, macro, watchlists (static placeholders), other commands, authentication, alerts, history backfill from BoZ, scheduled ingestion, frontend tests, a frontend lint step.

Known limitations:

- History is limited to what the JSON view serves (from 2026-01-22) plus quotes retrieved by this app. The filter's timezone is undocumented (harmless for whole-day windows given the 07:30-13:30 UTC slots).
- A few published rows are inconsistent and stored as published, e.g. buy > sell on 2026-03-19 07:30Z and 10:30Z; the live adapter and backfill share one source-selection policy (UTC hours 07/10/13, mirroring the BoZ site's latest-rate selection; its series chart is unfiltered, and unlike the site there is no fallback), so rows in those slots are trusted as published.
- A conflicting stored row is detected (safe warning) but not resolved: the live quote is returned unsaved and the stored row is kept; there is no reconciliation tool.
- T-bills: BoZ published tender 23/2026 (2026-11-12) with a duplicated 273-day record (the 182-day values filed under 273). It is rejected whole and reported in `skipped_tenders`; rows fall back to tender 22/2026.
- Redistribution and commercial-use terms of BoZ data are unreviewed.
- Migrations are plain SQL in `db/init` (applied automatically only on a fresh database volume).

## Treasury Bills (feat/boz-treasury-bills)
- Source: official BoZ Drupal JSON:API `/jsonapi/node/tbills_auction_results` (what the BoZ Treasury Bills page itself reads; public, no auth). One node per tender (`title` "22/2026", `field_tbills_auction_date`) with one included paragraph per tenor (91/182/273/364 days): ISIN, yield, discount. Field names differ per tenor and are matched strictly. Amounts, cut-off and yield ranges exist only in linked PDFs and are **not** modelled (no PDF parsing).
- `GET /api/v1/treasury-bills/latest` (newest row per tenor, each with its own auction date; `missing_tenors`, `skipped_tenders`) and `GET /api/v1/treasury-bills/{91D|182D|273D|364D}` (422 invalid tenor, 404 none recent, 504 timeout, 502 other). Fetches the 5 most recent tenders per request; a tender failing validation (bad number/date/ISIN/rate, duplicate tenor) is rejected whole, never repaired. `stale` = auction older than 21 days (heuristic).
- Persistence: `treasury_bill_auctions` (`db/init/003_treasury_bill_auctions.sql`, apply manually to existing volumes), unique on `(auction_date, tender_number, tenor_days)`; same conflict-aware rules as `fx_rates` (identical = no-op, different = rollback, never overwritten). Best-effort: the live response is returned if saving fails.
- No history endpoint yet (the API can page the archive; left for the next task). Commands: `TBILL ZM`, `TBILL 91D|182D|273D|364D`. No frontend test runner exists, so command parsing is verified by `tsc` only.

## Government Bonds (feat/boz-government-bonds)
- Source: official BoZ Drupal JSON:API `/jsonapi/node/bond_auction` (what the Government Bonds page reads; public, no auth). One node per auction (title `07/2026/BA`); `field_bond_auction_date` is an ISO datetime with explicit +02:00 offset, its calendar date being the auction date (every 2026 value is a Friday); one included `paragraph--<N>_year(s)_bond` per tenor (2/3/5/7/10/15) with ISIN, coupon, yield. Field names differ per tenor and are matched strictly; the tenor label must agree with the record type. Nodes before 12/2025 are empty placeholders (rejected as "no published tenors"). Amounts, cut-off coupon/yield and ranges exist only in PDFs and are **not** modelled.
- Reopenings: 7Y/10Y/15Y repeat the same ISIN and coupon across auctions with new yields; 2Y/3Y (and 5Y) are new ISINs. Identity is `auction_date + tender_number + tenor_years`, never the ISIN.
- `GET /api/v1/government-bonds/latest` and `/{2Y|3Y|5Y|7Y|10Y|15Y}`: newest row per tenor among auctions that contain it; `missing_tenors`; `skipped_tenders` (informational); per-row `newer_rejected_tenders` (rejected auctions dated after the row; non-empty = fallback; same semantics as T-bills). Discovery walks the BoZ archive newest-first, 50 auctions per page via the API's own `links.next` (same endpoint only), sequentially, stopping once all six tenors have a valid row or the archive ends (currently 3 requests: the archive is 110 tenders, 8 with data); a 10-page cap that is reached with tenors unfound and a `next` link still present fails closed (502). So `missing_tenors` means "not in the whole archive". A tenor can therefore come from an old auction (5Y: 02/2026); no stale flag. Duplicate auctions across pages collapse if identical and fail if conflicting. 422 invalid tenor, 404 none recent, 504 timeout, 502 other.
- **No `stale` flag** by design: the issuance calendar is a quarterly PDF (not machine-readable) and observed gaps between 2026 auctions were 21 to 63 days, so no defensible threshold exists. Each row shows its own auction date.
- Persistence: `government_bond_auctions` (`db/init/004_government_bond_auctions.sql`; apply to an existing volume with `docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/004_government_bond_auctions.sql`), unique on auction identity; same conflict-aware rules as T-bills; best-effort save.
- Live finding (2026-11-12): tender `03/2026/BA` has a malformed 15Y ISIN (`ZM100007709`, 11 characters). It is rejected whole and reported neutrally (older than every displayed row, so no fallback claim).
- Commands: `BOND ZM`, `BOND 2Y|3Y|5Y|7Y|10Y|15Y`; dashboard card `10Y GRZ BOND`. No history, no yield curve.
- **5Y caveat (verified 2026-11-12):** the only tenders that carry a 5Y (12/2025, 01/2026, 02/2026) are rejected whole because another record in each has a malformed ISIN, so 5Y has no valid observation and is reported missing (404 on `/5Y`), with those tenders listed in `skipped_tenders` and a UI note that it may exist only in rejected tenders. Rejection rules were not loosened. Empty legacy placeholders no newer than the newest valid auction are omitted from the rejected list.
