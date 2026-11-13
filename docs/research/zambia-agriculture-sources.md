# Zambia agriculture price sources: discovery and ingestion plan

Research only. Nothing here is implemented: no adapter, migration, API, UI, command or job exists for agriculture.
Discovery date: 2026-11-13. Everything below was read from public, unauthenticated URLs with plain GET requests, a
few requests per source, no login, no cookies and no automation of any authenticated flow. No raw payload was stored in
the repository. Counts and dates are as observed on that day.

## 1. Executive recommendation

| Source | Role (`market_type`) | Technical access | Reuse position | v1? |
|---|---|---|---|---|
| ZAMACE | `spot` | Public unauthenticated JSON exists, but is thin (10 rows, maize only) | Website terms prohibit copying, republishing and redistributing site material. **Blocked pending written permission** | No |
| Ministry of Agriculture bulletins | `national_average` | Official PDFs, text layer, stable recent layout | No terms found; treat as `review_required` | **Yes** |
| FRA | `procurement`, `government_sale` | Irregular announcements; the 2025 price is in one official statement; no soya | Government publication, no terms found; `review_required` | Second branch |

**Decision: Option C, Ministry only first, with FRA as the next branch.** ZAMACE is not Option A or B material:
the licence wording does not support our storage and display use (section 5). FRA data is one price per season
(maize only), the 2026 price is not in an official FRA document I could find, and soya is not procured at all
(section 4). So the Ministry bulletins are the only source that gives a defensible series for both commodities.
The schema in section 9 must still carry all four `market_type` values from day one.

Two facts the owner should know before the first branch: the newest Ministry bulletin found covers **January 2026**
(a ~10-month publication lag, section 3), and ZAMACE should be contacted rather than ingested (section 5).

## 2. ZAMACE findings

`https://www.zamace.co.zm/market-data` is a React single-page app; the HTML is a 484-byte shell. The bundle
(`/assets/index-*.js`) contains the API base `https://api.zamace.co.zm/api/v1` and a Phoenix websocket
(`wss://api.zamace.co.zm/api/v1/socket`, channel `room:market_data`, event `market_update`).

### Endpoints (all GET, no `Authorization` header, no cookie, no token needed; JSON)

| Route under `/api/v1/website/` | Returns | Fields of note |
|---|---|---|
| `markets/public_retrieve_zmw_spot_market_prices` | 10 rows, 2026-10-07 to 2026-10-26 | `date`, `location`, `commodity`, `price`, `currency`, `quantity`, `quality_grade`, `unit_measure` |
| `markets/public_retrieve_zmw_historical_spot_market_prices` | 10 rows, 2026-09-28 to 2026-10-06 | same fields |
| `markets/retrieve_public_commodity_market_data` | 397 open/closed offers | order metadata, `requested_unit_price`, `volume`, `delivery`, `quality`, `date_to_be_placed_on_platform`, `bids_on_offer` |
| `markets/retrieve_public_commodity_market_data_summary` | trading hours, tonnage totals | `trading_start_time`, `trading_end_time`, commodity lists |
| `commodities_market/retrieve_website_commodity_market_reports` | 5 weekly report PDFs (Jul to Sep 2026) | `report_date`, `report_name`, `pdf_url_link` |
| `markets/retrieve_exchange_operations_public_data` | not inspected | |
| websocket `room:market_data` | live bids and offers | not connected |

What is and is not exposed:

- **Spot prices:** only `maize`, grade `A`, unit `Metric Tonnes`, currency `zmw`, seven locations across the two windows (Chingola, Kabwe,
  Kalulushi, Lusaka, Mwembeshi, Monze, Ndola). Every observed `price` was `5000.0`, so the series carries almost no
  information today. **No soya beans.** These look like reported trade/reference prices, but the payload does not say
  whether a row is a last trade, settlement or reference price, so those cannot be told apart.
- **History:** none beyond the two fixed 10-row windows. No query parameters, pagination or date filters are used by the
  frontend (the calls are made with no arguments). Rows carry a UUID `id`, which allows de-duplication across windows,
  but a poller would have to run continuously to build any history.
- **Offers/bids:** `retrieve_public_commodity_market_data` is an order book of *asking* prices (395 maize, 1 black-eyed
  peas, 1 lutembwe cow peas), with per-order reference numbers and counterparty-level detail (`order_type`,
  `warehouse_receipt_id`, `bids_on_offer`). Asking prices are not transaction prices. A bid/offer feed must never be
  stored as a "price". It also carries trader-level order data, so it is not suitable for our `personal_data_expected = false`
  policy without review.
- **Weekly report PDFs:** one page, narrative; for week 38 of 2026 it states a Lusaka-delivered maize range of
  ZMW 4,500 to 5,000 per ton, volumes and settlement tonnage. Range text, not a structured series.
- **Access control:** none observed. The API answers `Access-Control-Allow-Origin: https://www.zamace.co.zm`, so
  browsers are restricted to ZAMACE's own origin; that is a first-party-use signal, not a block on server clients.
  `Cache-Control` is `private, must-revalidate`. `https://api.zamace.co.zm/robots.txt` returns 500;
  `https://www.zamace.co.zm/robots.txt` allows everything. Detailed prices behind the `dash.zamace.co.zm` login were
  not touched.
- **Timestamps:** only a date per row (no time). Grade, location and unit are present.

## 3. Ministry of Agriculture findings

Department of Agribusiness and Marketing. Index: `https://www.agriculture.gov.zm/monthly-market-information-bullet/`
(its "Download the latest" link points at an external host, not the Ministry's own file). Files sit under
`/wp-content/uploads/`. The Ministry's WordPress REST media search (`/wp-json/wp/v2/media?search=...`, public) lists
the files below.

**Access note:** the site serves an incomplete TLS chain (leaf only), so `curl` fails verification. I fetched the
Sectigo intermediate named in the certificate's AIA field and kept verification on; I did not use `-k`. An adapter
should pin the same intermediate or use a client that follows AIA, and never disable verification.

### Source matrix (all text PDFs with a text layer; no scans)

| Bulletin | Period covered | Published (upload) | Pages | Layout | Maize | Soya beans | Unit |
|---|---|---|---|---|---|---|---|
| No 020424 | Mar vs Apr 2024 | 2024-05-16 | 13 | A1 | yes | yes | ZMK/MT and ZMK/Kg |
| No 010425 | Dec 2024 vs Jan 2025 | 2025-04-10 | 13 | A2 | yes | yes | ZMK/MT and ZMK/Kg |
| No 020425 | Jan vs Feb 2025 | 2025-04-10 | 13 | A2 | yes | yes | ZMK/MT and ZMK/Kg |
| No 010425 (again) | Jan, Feb, Mar 2025 | 2025-04-27 | 13 | A3 | yes | yes | ZMK/MT and ZMK/Kg |
| No 020430 | Apr 2025 to Jan 2026 | 2026-05-21 (PDF created 2026-05-04) | 11 | B | yes | yes | ZMW/KG |

(The Ministry's search surfaced no bulletin for May 2024 to Dec 2024 or Feb 2026 onwards. That does **not** prove they
do not exist: the search terms were `bulletin`, `market`, `price`, `weekly` and a few month names.)

Layout families:

- **A (Apr 2024 to Mar 2025):** "NATIONAL COMMODITY AVERAGE PRICES" table with *both* ZMK/MT and ZMK/Kg per period
  (e.g. maize 8,371.98 / 8.37), previous and current period, and a trailing `% CHANGE`; A3 has three monthly columns.
- **B (Apr 2025 to Jan 2026):** one `Table 1` with December and January in ZMW/KG, then `Table 3`, a month-by-month
  table of **ten months (April to January)** with a `% change` after each month. This single document therefore holds
  a monthly national series for April 2025 to January 2026.

What a parser must handle (observed):

- **Unit varies by row, not by bulletin:** grains in ZMW/KG (layout B) or ZMK/MT and ZMK/Kg (layout A); meal in
  ZMW/25kg; fertilizer ZMW/50kg; seeds ZMW/5, 10, 20 or 25 kg. The ISO code printed in 2024 and early 2025 is `ZMK`
  although the values are kwacha (post-2013 rebased), and `ZMW` from layout B. Treat both as ZMW only after
  confirming the magnitude matches (they do: maize ~8 to 9 kwacha/kg in both), and record the printed label.
- **Grain vs seed are separate rows:** `Maize` vs `Maize Seed` (early/medium/late maturing, per 10 kg), `Soya Beans` vs
  `Soya Bean Seed` (per 25 kg). Row labels are not identical across families ("Maize seed - Early Maturing" vs
  "Early Maturing" under a "Maize Seed" heading), so the parser must key on the *section unit header* too.
- **`% CHANGE` is unreliable:** in layout A1 maize goes 8.37 to 8.69 yet prints `0.00`, soya 7.04 to 7.80 prints
  `0.01` (a fraction, not percent, and rounded wrongly). Never ingest the printed `% CHANGE`; compute it if needed.
- **Bulletin numbers are not unique** (`010425` is both the January and the March 2025 bulletin). Identify a bulletin
  by its stated period and file URL, not its number.
- **Prices are indicative:** every bulletin carries "The Price data in this bulletin are only indicative weekly
  averages subject to adjustments within the course of each week." Store that this is an *indicative average*, never an
  executed trade or exchange price.
- **Volatility to preserve, not clean:** layout B Table 3 shows soya at 19.58, 12.49 and 7.89 ZMW/kg for April to June
  2025, against 14.20 for March 2025 in layout A3. Ingest as published and let users see the source; do not smooth.
- **Scope is national only.** No provincial or district split appears in the tables inspected. Weekly and provincial
  bulletins exist per the text ("district, provincial and national levels") but none was found online.

**Date model (derivable from the source):** the table columns name months ("DECEMBER", "JANUARY", "CURRENT - APRIL 24"),
and the header states an end date ("Bulletin No: 020430 - 30th April 2025 to 31st January 2026",
"AS AT 30/04/24"). Observation granularity is therefore **a calendar month**: `period_start` = first day,
`period_end` = last day. The month's price is an average of weekly averages, so there is no single trade date, and
the upload date is a different thing (2 weeks to about 4 months after the period end in the files found).

**Recency caveat:** at time of writing (2026-11-13) the newest bulletin found covers January 2026, uploaded in May 2026.
A "latest national average" view built on this source would be ten months old. The UI must show the period, not imply
currency.

## 4. FRA findings

`https://fra.org.zm`. A WordPress site; the public REST API lists 7 posts (2025-06 to 2026-03) and a handful of PDFs.
Procurement is announced by gazette notice; sales are announced as programmes run via ZAMACE.

| Publication | Type | Price in it | Notes |
|---|---|---|---|
| "Ministerial Statement on Farmer Payments 2025" (statement of 4 Nov 2025, posted 2025-11-05; PDF `MINISTRIAL-STATEMENT-MAIZE-PURCHASES-2025.pdf`) | procurement, 2025 season | **K6,800 per MT or K340 per 50 kg bag** (white maize). Also states purchases of 1,663,934 MT valued at K11,314,750,520 as of 31 Oct 2025 | An official statement, not an FRA price notice; it describes the *projected* purchase price. The season commenced 30 June 2025. |
| "2025 Gazette Notice and List of Depots" (2025-06-12) | procurement plan, 2025/26 | none | Commodities: non-GMO white maize and paddy rice only. States the exercise opened 1 May 2025 and closes 31 Oct 2025. |
| "Maize Sales Programme for 2025" (advert first published 17 Oct 2025) | government sale | none | Bid-based via ZAMACE; "bids should specify your preferred quantities and competitive prices". |
| "FRA Maize Sales Programme for 2026" (2026-03-26) | government sale | none | Up to 700,000 MT non-GMO Grade A white maize, sold via ZAMACE bids. |
| "FRA Early Maize Contracts Publication 10.07.2025" (xlsx) | contracts | not inspected | Contract listing, not a price series. |

Findings:

- **Maize procurement 2025:** K340 per 50 kg (6.80 ZMW/kg) is in an official document, but the effective date is only
  "season commenced 30 June 2025" (a statement, not a notice of effective date). Do not infer a date range.
- **Maize procurement 2026:** press reports (Zambia Monitor, Lusaka Times) put the 2026 floor price at **K347 per
  50 kg bag (K6.94/kg)**, non-GMO Grade A white maize. I did **not** find the FRA notice itself; treat as unverified
  until an FRA or Ministry document is found.
- **Soya bean procurement:** none. FRA's designated commodities in the notice are maize and paddy rice only.
  There is no FRA soya series.
- **Sales programme prices:** none published; the price is discovered by bids on ZAMACE. A `government_sale` row would
  have no price to store, only quantity and terms. Skip it in v1.
- **Grade/variety** is explicit in FRA text ("non-genetically modified white maize", "Grade A" for sales): keep as a
  `grade` and `variety` attribute, not collapsed.

## 5. Source and licensing constraints

**ZAMACE.** The public Terms and Conditions page (`/terms-conditions`, bundled in the site JS) says: "Unless otherwise
stated, Zambian Commodities Exchange Ltd owns the intellectual property rights for all material on this website", and
lists as prohibited: "Republish material from ZAMACE Website", "Sell or sub-license material", "Reproduce or copy
material", "Redistribute content". The footer reads "All rights reserved". The page also says visiting the website
implies acceptance of the terms. I am not drawing a legal conclusion beyond the wording: storing and re-displaying
prices from this feed is a form of copying, and the terms do not grant an exception for market data. The terms are
silent on commercial use and on API access.

Classification: **ingestion blocked pending permission.** Provider policy would otherwise be
`redistribution = review_required`, `commercial_use = review_required`. Recommend the owner write to ZAMACE
(`info@zamace.co.zm`, listed on FRA's sales programme) asking for written permission to store and display the public
spot-price endpoint with attribution, and whether a documented feed exists. Record the roadmap line
"ZAMACE spot integration pending permission / suitable public access".

**Ministry of Agriculture.** No terms of use were found on the bulletin pages or in the PDFs; only the disclaimer
that prices are indicative and the Ministry takes no liability. Official government statistics, but the reuse position
is not stated: use `redistribution = review_required`, `commercial_use = review_required`, attribute the Ministry, and
link the source PDF.

**FRA.** No terms found. Same classification. The documents are government announcements.

No source requires authentication for the files above. No personal data is expected in the Ministry or FRA documents
(FRA statements aggregate farmers by count; ZAMACE order data does carry counterparty-level order detail, see section 2).

## 6. Commodity semantics

Stable identifiers: `MAIZE` (grain) and `SOYA_BEANS` (grain). Not combined with each other, and not with seed.

- **Grain vs seed.** Bulletins publish both. Grain rows are `Maize` and `Soya Beans` priced per kg (ZMW/KG or
  ZMK/Kg). Seed rows are `Maize Seed` (per 10 kg, three maturity classes) and `Soya Bean Seed` (per 25 kg).
  A parser must **allow-list** grain labels (`Maize`, `Soya beans`/`Soya Beans`) and reject anything containing
  `seed`, `meal`, `flour`, `bread`, `fertilizer`. A guard test should fail the import if a grain label shows a per-25 kg
  or per-10 kg unit.
- **Quality dimensions that exist today:** ZAMACE: `quality_grade` (`A`), `location`, `delivery` point, `quality`;
  FRA: "non-GMO white maize", "Grade A" (sales). Ministry: none (a single national figure with no grade or variety).
  Not found in any source: moisture, contract type, warehouse (beyond `warehouse_receipt_id` on ZAMACE orders).
  Because grade, variety and delivery location *do* appear in spot and FRA data, the schema keeps nullable `grade`,
  `variety` and `delivery_location`, and does not discard them to get one national price.

## 7. Date semantics

Never use a PDF's upload date as the observation date.

| Source | Derivable | Not derivable |
|---|---|---|
| Ministry | the observed *month* (column header and "as at" date), plus the file's upload date | a trade date; the exact weeks averaged |
| FRA | the season (e.g. 2025 crop marketing season), a statement date (4 Nov 2025), a notice date | an effective-from date for the price. Do not infer it |
| ZAMACE | a per-row `date` (no time) | trade time, and the price type (last trade vs reference) |

Fields: `period_start`, `period_end` (Ministry and FRA season), `observed_date` (ZAMACE row date only, otherwise null),
`publication_date` (the document's own date if printed, else the upload date with `publication_date_source` noted),
plus `retrieved_at`. A single "observation date" for the Ministry would be a lie; use the period.

## 8. Units and normalization

Store the published figure and unit unchanged and add one derived comparable field:

```text
source_price            Decimal, exactly as printed
source_unit             enum: kg | 10kg_bag | 25kg_bag | 50kg_bag | tonne | ...
normalized_zmw_per_kg   Decimal, derived only when the unit is explicit
```

Rules:

- Conversion is arithmetic only: K340 / 50 kg gives `source_price = 340`, `source_unit = 50kg_bag`,
  `normalized_zmw_per_kg = 6.80`; K6,800 / MT gives 6.80. Money is `Decimal`, never `float`; use the project's
  `round_half_up` only for display, and keep full precision in storage.
- Normalize only when the unit text is explicit (`ZMW/KG`, `ZMK/Kg`, `ZMK/MT` where "MT" is metric tonne = 1,000 kg,
  `per 50kg bag`). Bag units are normalized only when the bag weight is printed. Unknown bag sizes, "per bag",
  "per ton" without a stated tonne, or an unclear grade are stored with `normalized_zmw_per_kg = NULL`.
- Where a bulletin prints both ZMK/MT and ZMK/Kg (layout A), the two must agree (price per MT / 1,000 = price per
  kg within rounding). A mismatch fails the import for that row.
- Printed currency code `ZMK` is a legacy label. Store `source_currency = 'ZMW'` and keep the printed label in a
  `source_currency_label` column or in the import notes. Do not convert anything.

## 9. Proposed schema (not created)

```text
zambia_agricultural_prices
  id
  commodity              MAIZE | SOYA_BEANS
  market_type            spot | national_average | procurement | government_sale
  provider               zamace | moa | fra
  observed_date          date, null unless the source gives a trade/row date (ZAMACE)
  period_start           date, null for spot
  period_end             date, null for spot
  publication_date       date, null if not printed
  season                 text, FRA only, e.g. "2025"
  grade                  text, nullable (ZAMACE "A", FRA "Grade A")
  variety                text, nullable ("white", non-GMO flag)
  location_scope         national | province | market | depot
  location               text, nullable
  delivery_location      text, nullable
  price_basis            enum: indicative_average | reported_trade | reference | announced_price
  source_price           numeric
  source_currency        'ZMW'
  source_unit            text enum
  normalized_zmw_per_kg  numeric, nullable
  source                 provider/publication name
  source_url             the exact PDF or endpoint
  source_period_label    text, e.g. "JANUARY", printed label for audit
  source_record_id       text, nullable: the source's own row id (ZAMACE spot rows carry a UUID `id`)
  retrieved_at           timestamptz
  -- no universal UNIQUE key: identity is per market_type, see "Observation identity" below
```

Only fields justified by discovery are included. Not included: `% change` (unreliable in the source), bids/offers and
order references, volume (ZAMACE only; add if ZAMACE is later permitted), raw payload (never stored). The
`source_record_id` exists because discovery found it (section 2); no `observed_at` is proposed because ZAMACE rows carry a date only. The
`price_basis` column is how the model keeps "indicative average" distinct from "executed trade" for the Ministry
disclaimer. Reusing one table is fine because every row carries `market_type`; queries must always filter on it.

### Observation identity (per market type)

A single `UNIQUE` over the nullable columns is wrong here for two reasons. The datasets have different time semantics
(a monthly period, a crop season, a trade date), so most time columns are legitimately NULL for any one market type.
And PostgreSQL (including 17, the version in `compose.yml`) treats NULLs as **distinct** in an ordinary `UNIQUE`
constraint, so two rows that differ only by NULL columns (national `location`, absent `grade`, FRA `period_start`)
would never collide and duplicates would pass. Use one **partial unique index per `market_type`**, with
`NULLS NOT DISTINCT` (PostgreSQL 15 and later) so a NULL dimension that is part of the identity compares equal. The
partial index also encodes each market's own meaning.

Never part of any identity: `retrieved_at`, `publication_date`, `source_url`, `source_price`, `normalized_zmw_per_kg`.
Behaviour on a re-import of the same identity: same price is idempotent (no-op); a different price is a **conflict /
source revision that needs review**, never a silent overwrite and never a second row.

| `market_type` | Time identity | Identity (partial unique index, `NULLS NOT DISTINCT`) |
|---|---|---|
| `national_average` (Ministry) | the reporting period | `(provider, commodity, period_start, period_end, grade, variety, location_scope, location, source_unit)` where `market_type = 'national_average'` |
| `procurement` (FRA) | the crop marketing season | `(provider, commodity, season, grade, variety, location_scope, location, source_unit)` where `market_type = 'procurement'` |
| `government_sale` (FRA) | the programme/season or published sale period, whichever the source supplies | `(provider, commodity, season, grade, variety, location_scope, location, source_unit)` where `market_type = 'government_sale'`, plus the sale period if one is published |
| `spot` (ZAMACE) | **unresolved**, see below | provisional: `(provider, source_record_id)` where `market_type = 'spot'` |

- **`national_average`.** A re-published bulletin for the same month and unit is the same observation. A different
  value for it is a revision to review. The Ministry's national rows have `location_scope = 'national'` and NULL
  `location`, `grade`, `variety`, which is exactly why `NULLS NOT DISTINCT` is required.
- **`procurement`.** Keyed by `season` ("2025"), because `observed_date`, `period_start` and `period_end` are NULL for FRA
  announcements and the season is all the source gives (section 7). Different seasons never collapse. Discovery found
  one procurement price per commodity per season, so no `programme` field is added; if FRA is later seen to issue two
  procurement programmes for the same commodity and season, add `programme` to this index at that point.
- **`government_sale`.** Kept apart from procurement even though FRA publishes both: a sale programme is a different
  event with different semantics, units and (today) no published price (section 4). Nothing is implemented for it in
  v1, so its final key (programme year versus a published sale window) is settled when a priced sale source exists.
- **`spot`.** ZAMACE spot rows (`public_retrieve_zmw_*spot_market_prices`) expose **a date only** (`"date":
  "2026-10-26"`, 10 characters on all 20 rows seen), **not a time of day**. So there is no `observed_at timestamptz`,
  and none is proposed: that would invent precision the source does not provide. The natural key
  `(date, location, commodity, quality_grade)` is **not unique**: across the 20 rows observed, Kabwe, maize, grade A on
  2026-10-01 appears twice. The rows do carry a UUID `id` (20 distinct in 20 rows), which is why `source_record_id`
  is proposed as the provisional identity. **Unresolved, to settle before any spot implementation:** whether that `id` is
  stable across polls and across the "current" and "historical" endpoints (no row overlapped between the two windows,
  so this could not be tested), and whether two same-date rows are distinct trades or a re-issued price. If the `id`
  is not stable, spot deduplication has no sound key and spot should not be ingested. This is moot until ZAMACE grants
  permission (section 5).

## 10. Proposed commands and UI semantics

| Command | Meaning |
|---|---|
| `AGRI ZM` | grid of the two commodities by market type, one row per source, with period and `price_basis` |
| `MAIZE ZM`, `SOYA ZM` | **composite detail** with three labelled blocks: Exchange/market, National average, FRA procurement. Never silently picks one source and never defaults to FRA |
| `MAIZE MARKET`, `SOYA MARKET` | `spot` only (inactive until ZAMACE permission; show "no permitted source" rather than substituting another) |
| `MAIZE AVG`, `SOYA AVG` | Ministry `national_average` series (the v1 vertical slice) |
| `MAIZE FRA` | FRA procurement/sale records (maize only; `SOYA FRA` would show "FRA does not procure soya beans") |

These semantics hold up. Two additions: show the period ("Jan 2026"), not "latest", on every Ministry value, and show
the seed/grain distinction by listing only grain.

## 11. Provider policy recommendations

| Provider id | authentication | personal_data_expected | normalized_storage_allowed | raw_storage | attribution | redistribution | commercial_use | required timestamps |
|---|---|---|---|---|---|---|---|---|
| `zamace_commodity_market` | none for public endpoints; **login endpoints out of scope** | false for `*_spot_*`; order-level detail in the offers feed (do not ingest it) | **no, pending permission** | no | "Zambian Commodities Exchange" | review_required (terms prohibit) | review_required | `observed_date`, `retrieved_at` |
| `moa_agricultural_market_bulletin` | none | false | yes (normalized rows only) | no (no PDFs stored) | "Ministry of Agriculture, Department of Agribusiness and Marketing" + PDF URL | review_required | review_required | `period_start`, `period_end`, `publication_date`, `retrieved_at` |
| `fra_procurement_prices` | none | false | yes (price, season, unit) | no | "Food Reserve Agency" or the Ministry statement it came from | review_required | review_required | `season`, `publication_date`, `retrieved_at` |

## 12. v1 implementation recommendation (first branch)

Branch `feat/moa-market-bulletin-prices`:

1. Discover bulletin files through the Ministry's public WordPress media endpoint; fetch with full TLS verification
   (handle the missing intermediate).
2. Parse **layout B** (`Table 3`, monthly ZMW/KG; April 2025 to January 2026 from one PDF) and **layouts A1 to A3**
   (ZMK/MT + ZMK/Kg) behind strict, fail-closed parsers, with allow-listed grain rows, the seed guard, unit
   consistency check and a refusal to ingest the printed `% CHANGE`.
3. Store `MAIZE` and `SOYA_BEANS` as `market_type = national_average`, `price_basis = indicative_average`, with
   `period_start`/`period_end` and the source URL.
4. Backend only plus a small read API; the UI waits for a later branch.

Second branch: FRA. It would store one 2025 `procurement` maize record from the Ministerial Statement text, and
a 2026 record only once an FRA or Ministry document with the price is found. Third: ZAMACE only after written
permission, at which point `market_type = spot` rows go in with a periodic poller.

Why not Option A or B first: ZAMACE is blocked on terms, and its public feed is 10 rows of maize with one repeated
price; FRA has one price per season, so adding it first would not produce a series, only a number.

## 13. Historical coverage

| Series | Status |
|---|---|
| MAIZE national average | **Confirmed document archive**: Apr 2024, Jan, Feb, Mar 2025, and Apr 2025 to Jan 2026 (monthly, one combined PDF). Not verified: May to Dec 2024, anything before Apr 2024 or after Jan 2026. |
| SOYA national average | Same documents and dates as maize. Not verified beyond them. |
| MAIZE FRA procurement | 2025 season confirmed (official statement, K340/50 kg). 2026 reported by media only. Earlier seasons **not verified**. |
| SOYA FRA procurement | **Does not exist** per FRA's notice (maize and paddy rice only). |
| ZAMACE spot | **Confirmed machine-readable**, but only a rolling 10-row window per endpoint (Sept 28 to Oct 26, 2026 across the two); history would need continuous polling. Not usable pending permission. |

No machine-readable (CSV/JSON/XLSX) national-average feed was found at the Ministry.

## 14. Unresolved questions

1. **ZAMACE:** will it grant written permission to store/display the public spot endpoint? Does a documented API exist?
   Is the spot `price` a trade, reference or settlement price, and why is every maize price 5,000?
2. **Ministry:** are bulletins after January 2026 (and May to Dec 2024) published somewhere other than the WordPress
   media library? Who owns the terms of reuse? Is there a weekly series online?
3. **FRA:** where is the official 2026 purchase-price notice (K347/50 kg per press)? What is the effective date of the
   2025 price?
4. The `ZMK` label in 2024 to early 2025 bulletins: confirm with the Ministry that it means kwacha (values suggest it does).
5. Soya national averages swing from 7.89 to 19.58 ZMW/kg inside three months of 2025; confirm whether this is real or
   an artefact of sampling before the UI presents it as a trend.
