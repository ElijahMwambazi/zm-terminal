# BoZ commercial bank USD/ZMW rates: source discovery

Research only (2026-11-22, contract finalized after a bounded follow-up check). Nothing is implemented; no code, migration or database was touched. Rights status for every statement below: `review_required`.

## 1. Decision

**APPROVED FOR LOCAL IMPLEMENTATION** (local/internal only; **not** approved for public display, API redistribution or commercial use; see section 11).

The page's own machine-readable source is a plain, unauthenticated, same-origin Drupal JSON:API endpoint on `www.boz.zm` that returns the bank-by-bank table, including a BoZ-published market-average row, as decimal strings. No HTML scraping is needed. The follow-up check (sections 4 and 9) changed the design in four ways, all written into the recommendations: **several nodes can exist for one date** (BoZ publishes progressively, one node per publication step), **blank cells are legitimate** (slots not yet published), **the table layout and bank names changed between January and November 2026**, and **decimal scale is not fixed** in the older layout. The first-draft rule "exactly four fractional digits" was therefore withdrawn (section 5).

## 2. User value

A single place to see what each licensed bank publishes for USD/ZMW at the three daily slots, and how that compares with the BoZ-published market average: who pays the most for USD, who charges the least for USD, and how wide each bank's displayed spread is. This is a different question from the existing `FX USDZMW` quote (one BoZ reference rate) and from the monthly averages.

## 3. Official source

- Page: `https://www.boz.zm/markets-securities/commercial-banks-rates` (Angular single-page app; the HTML is a shell of 9,929 bytes with no data).
- The page route is the lazy component `app-daily-commercial-banks-page`; its service (`CommercialBankRatesService` in `main.98bf04ed93a98a0c.js`) calls the endpoint below. The title the page renders is "ZMW/USD Commercial Bank Rates for <dd/mm/yyyy>" with a date picker.

## 4. Network/source discovery

Requests made to BoZ (exhaustive; nothing else was contacted, no bank website, no third party):

| # | URL | Result |
|---|---|---|
| 1 | `GET https://www.boz.zm/markets-securities/commercial-banks-rates` | 200 `text/html`, shell only |
| 2 | `GET https://www.boz.zm/main.98bf04ed93a98a0c.js` | 200, 2.7 MB bundle (read to find the endpoint and the page's own logic) |
| 3 | `GET https://www.boz.zm/jsonapi/node/commercial_bank_daily_rates?sort=-created&page[limit]=1` | 200 `application/vnd.api+json`, 6,870 bytes |
| 4 | `GET https://www.boz.zm/jsonapi/node/commercial_bank_daily_rates?filter[commercial_bank_rates_date][operator]=BETWEEN&filter[commercial_bank_rates_date][value][0]=2026-11-13T00:00:00&filter[commercial_bank_rates_date][value][1]=2026-11-13T23:59:59&sort=-created&page[limit]=1` | 200, one record for that date |

Endpoint contract as observed:

- **Method/URL:** `GET https://www.boz.zm/jsonapi/node/commercial_bank_daily_rates`; the API base is `apiBaseUrl: "https://www.boz.zm"` in the site's production config, and the site CSP `connect-src` allows `https://www.boz.zm`, so it is same-origin.
- **Latest:** `sort=-created&page[limit]=1` (what the page uses on load). **By date:** the `commercial_bank_rates_date` BETWEEN filter above with `T00:00:00`..`T23:59:59` of the requested calendar date (what the date picker uses), also `sort=-created&page[limit]=1`.
- **One request per view** (the page makes exactly one request for the latest table and one per date selection). The response carries `links.next` (JSON:API offset/limit pagination) even with `limit=1`; pagination is not needed for one record, and the default page size and maximum were **not** tested.
- **Auth/session:** none. The requests were plain `curl` with no cookie, token or special header; the server sets only a load-balancer `cookiesession1` cookie, which was not sent back and is not required. No redirect occurred (no `-L`; direct 200). `Cache-Control: must-revalidate, no-cache, private`. Whether a missing or generic `User-Agent` is rejected was not tested; the existing adapters send an identifying `User-Agent` and a new adapter should do the same.
- The bundle also lists other BoZ data endpoints (for example `api/node/historical_daily_zmw_usd`, `api/v1/views/boz_zmw_usd_daily_exchange_rates`); they were **not** contacted.

**Bounded follow-up requests (second pass; all `www.boz.zm`, all 200 `application/vnd.api+json`, same base URL as above):**

| # | Query (appended to `/jsonapi/node/commercial_bank_daily_rates`) | Purpose |
|---|---|---|
| 5 | `?sort=created&page[limit]=1` | earliest record by creation |
| 6 | date filter for `2026-11-14` (a Saturday), `sort=-created&page[limit]=1` | direct missing-date behaviour |
| 7 | date filter for `2026-01-28`, `sort=-created&page[limit]=5` | node count for the earliest date |
| 8 | date filter for `2026-11-20`, `sort=-created&page[limit]=5` | node count for a recent date |

Total BoZ requests in this research: 8 (4 original, 4 follow-up). No bank websites, third parties or Drupal revision routes were contacted.

## 5. Payload contract

JSON:API document: `jsonapi`, `data[]`, `links`. One `data` item of type `node--commercial_bank_daily_rates` per published node. Observed fields:

- **Top-level `data[].id`**: the Drupal node UUID (stable across that node's revisions); `data[].links.self` carries a `resourceVersion=id:<vid>` URL.
- Attributes: `drupal_internal__nid` (integer node id), `drupal_internal__vid` (integer **revision** id: each saved revision of a node has a different `vid`; the `self` link's `resourceVersion=id%3A<vid>` ties the two together), `title`, `created`, `changed`, `revision_timestamp` (all UTC `+00:00`; `changed` equalled `revision_timestamp` in every record seen), `commercial_bank_rates_date` (ISO datetime with explicit `+02:00` offset), `status`, `moderation_state` (`published`), `langcode`, `path`.
- `field_commercial_bank_rates`: `{ "value": {...}, "format": null, "caption": "COMMERCIAL BANK RATES" }`. `value` is a Drupal tablefield: an object keyed `"0".."N"` (table rows) plus a `caption` string key; each row is an object keyed `"0".."12"` (13 string cells) plus `"weight"`.
- Titles are not reliable identifiers (`BOARDRATES 13 11 26 1530`, `BOARDRATES 20 11 26`, `BOARDRATES 20 22 26 0930` (sic, month typed as 22), `Bank of Zambia Commercial Bank Rates 28-01-26`). Never parse them.

Cell layout, both variants (slots and quartets sit in the same cells): cell 0 bank name; cells 1-4 slot `9:30` (interbank buy, interbank sell, retail/board buy, retail/board sell); cells 5-8 slot `12:30`; cells 9-12 slot `15:30`. Three header rows then one row per bank then a market-average row.

| | Nov 2026 layout ("v2") | Jan 2026 layout ("v1", earliest record) |
|---|---|---|
| row 0 | `Bank Name`, slot labels `9:30`/`12:30`/`15:30` | cell 0 is a date string with a BOM and no-break space (`28-01-2026`), same slot labels |
| row 1 | `INTERBANK` / `BOARD RATES` per slot | `Bank Name`, then `Interbank Rates` / `Retail Rates` per slot |
| row 2 | `Buy`, `Sell` x 6 | same |
| market average | `MARKET AVERAGE`, `weight` 99 | `Market Average`, `weight` 17 (sequential) |
| bank names | e.g. with `PLC`/`LIMITED` suffixes, space-padded | shorter, mostly without those suffixes, different padding |
| decimal strings | `dd.dddd`, 180/180 cells | 1 to 4 fractional digits (trailing zeros dropped), 180/180 cells numeric |

So the parser must locate slots by the header labels rather than assume row text, treat the group labels `INTERBANK`/`Interbank Rates` and `BOARD RATES`/`Retail Rates` as the two known pairs, and fail loudly on anything else.

**Decimal contract (revised).** The first draft said every cell was `dd.dddd` and that the parser should require exactly four fractional digits. Both inspected November records did use two integer digits and four decimals (an observation, not a guarantee), but the January record shows trailing zeros dropped (a value such as `25.9000` appears as `25.9`), so scale is **formatting, not precision**. Production grammar, applied to every published rate cell:

```text
JSON string, no surrounding whitespace, ^[0-9]+\.[0-9]{1,4}$ , Decimal > 0
```

No exponent, sign, comma, `NaN`/`Infinity`, bare integer or more than four fractional digits; no cap on integer width. Store the exact published string and a `NUMERIC`; compare values numerically for conflict detection (`9.9` equals `9.9000`). A strict "exactly four digits" rule would reject the earliest record. If a stricter layout-specific rule is wanted later it can be applied to v2 only. Never go through a float.

**Blank cells are legitimate.** Nodes published early in the day have empty-string cells for slots not yet published (section 9). Per slot: all four cells blank = slot not published (no row), all four numeric = published, anything else (a partial quartet or non-numeric text) = reject the whole node. A bank row whose name cell is blank, or a duplicate normalised name inside a node, is also a reject.

Synthetic example of the v2 shape (values invented):

```json
{"data":[{"type":"node--commercial_bank_daily_rates","id":"00000000-0000-4000-8000-000000000000","attributes":{
  "drupal_internal__nid":1,"drupal_internal__vid":2,"title":"BOARDRATES 01 01 30",
  "created":"2030-01-01T13:00:00+00:00","changed":"2030-01-01T13:00:08+00:00","revision_timestamp":"2030-01-01T13:00:08+00:00",
  "commercial_bank_rates_date":"2030-01-01T15:00:00+02:00",
  "field_commercial_bank_rates":{"value":{
    "0":{"0":"Bank Name","1":"9:30","5":"12:30","9":"15:30","weight":"0"},
    "1":{"1":"INTERBANK","3":"BOARD RATES","weight":"1"},
    "2":{"1":"Buy","2":"Sell","3":"Buy","4":"Sell","weight":"2"},
    "3":{"0":"EXAMPLE BANK PLC     ","1":"10.0000","2":"10.1000","3":"9.9000","4":"10.2000","5":"","6":"","7":"","8":"","weight":"3"},
    "4":{"0":"MARKET AVERAGE","1":"10.0000","weight":"99"},
    "caption":"COMMERCIAL BANK RATES"}}}}]}
```

## 6. Time/date semantics

- **Slot labels** are `9:30`, `12:30`, `15:30` (no timezone, no date): table-header labels, not timestamps. The existing ZM Terminal USD/ZMW adapter treats BoZ's slots as Africa/Lusaka (UTC+2, no DST); that is the defensible reading but this payload does not state it. Do not store invented UTC timestamps as source facts; a derived `slot_local_time` + `Africa/Lusaka` mapping is an application choice, kept separate.
- **Observation date:** the calendar date of `commercial_bank_rates_date` read at its own `+02:00` offset. The date matched the title in every record where the title was well formed.
- **The time part of `commercial_bank_rates_date` is entry time, not a slot time.** On 2026-11-20 the three nodes carry 09:49, 14:32 and 15:36 (+02:00), each shortly after the newest slot that node contains. The earliest record (2026-01-28) shows `10:14 +02:00` although its `created` is `17:44 UTC` and all slots are filled, so in legacy records it is not even reliably ordered against `created`. Use it only for the date; use `created`/`changed` for ordering and provenance.
- **Weekends/holidays:** the server applies no fallback (section 9): a date with no node returns an empty `data` array. The page's own weekend roll-back and five-day retry are client-side convenience.

## 7. Bank identity

The payload gives **only a display name** (row cell 0), no institution ID. Store both:

- `institution_name_raw`: the cell string exactly as received (including padding; for the market-average row too).
- `institution_name`: the identity, produced by the same normalisation the BoZ page applies: collapse runs of whitespace to one space and trim the ends. Used for uniqueness and queries. No case folding, no fuzzy matching, no legal-entity mapping and no curated aliases in Phase 1.

Names observed in the November records (all uppercase, 14 banks plus the average): `ABSA BANK ZAMBIA PLC`, `ACCESS BANK ZAMBIA LIMITED`, `BANK OF CHINA (ZAMBIA) LIMITED`, `CITIBANK ZAMBIA LIMITED`, `ECOBANK ZAMBIA LIMITED`, `FIRST ALLIANCE BANK ZAMBIA LIMITED`, `FIRST CAPITAL BANK ZAMBIA LIMITED`, `FIRST NATIONAL BANK ZAMBIA LIMITED`, `INDO-ZAMBIA BANK LIMITED`, `STANBIC BANK ZAMBIA LIMITED`, `STANDARD CHARTERED BANK PLC`, `UNITED BANK FOR AFRICA ZAMBIA LIMITED`, `ZAMBIA INDUSTRIAL COMMERCIAL BANK`, `ZAMBIA NATIONAL COMMERCIAL BANK PLC`. The count is an observation, not a contract. **Consequence:** the January 2026 record spells several of the same banks differently (shorter names), so by-name identity is **not continuous across the layout change**; with no aliases in Phase 1, a bank's history is split at that point and the UI must not imply otherwise. The market-average row is detected as a normalised name equal to `market average` compared case-insensitively (`MARKET AVERAGE` v2, `Market Average` v1), flagged `is_market_average` and never treated as a bank.

## 8. Market-average semantics

`MARKET AVERAGE` is an **upstream-published row** inside the CMS table (strings in the payload, the same 12 cells), **not** calculated by the BoZ JavaScript: the page's mapper treats it as just another bank row and applies no arithmetic. How BoZ computes it (simple versus weighted, which banks) is not stated anywhere in the payload or the page code and **must not be reverse-engineered**. ZM Terminal should store and show it as `source-published market average`, separately from banks, never as a computed value, and never recompute it. Any deviation of a bank from it should be named "difference from the BoZ-published market average".

## 9. Historical availability and multiplicity (verified)

- **Earliest record:** the query `sort=created&page[limit]=1` returned a node created `2026-01-28`, observation date **2026-01-28** (`nid` 1873, `vid` 2438), in the v1 layout (section 5): 18 table rows, 13 cells per row, three slot labels, 14 banks plus `Market Average`, all 180 rate cells numeric. **Verified lower bound: bank-by-bank JSON:API history observed back to 2026-01-28** (earliest by creation order; a record with an earlier observation date but a later creation date was not ruled out, and completeness between the endpoints is not claimed). That is about ten months, not a long history.
- **Missing date:** a direct query for Saturday 2026-11-14 returned HTTP 200 with `"data": []` (and only a `self` link): empty data, no fallback record, no error.
- **Several nodes per date exist.** 2026-11-20 returned **3 nodes** (`nid` 115760, 115764, 115767; created 07:49, 12:33 and 13:36 UTC). They are progressive publications: the first has only the `9:30` slot filled (the rest blank strings), the second `9:30` and `12:30`, the third all three. The filled slots matched across nodes where both were filled for `9:30`. 2026-01-28 returned a single node. The page's `sort=-created&page[limit]=1` therefore shows the newest node, which is normally the most complete.
- **Revisions:** in every record seen `changed` is later than `created` (4 s to 2.5 h) and equals `revision_timestamp`, so nodes are saved again after creation and the `vid` identifies the revision seen. Revision history was not requested; whether a stored node can be corrected later is open.
- **No backfill is approved by this check.** The record count for a date can be limited only by the page limit; the importer must detect `links.next` and fail rather than silently drop nodes.

## 10. Existing annual aggregate series (separate, not combined)

BoZ's statistical booklets contain an annual **Commercial Banks' Exchange Rates (K/Foreign Currency), End Period** table (the project brief says year-end buy/sell for USD, GBP, EUR and ZAR). This task did **not** fetch or open it; the only existing project note (`docs/research/boz-deeper-fx-history.md`) records that the 2025 booklet has `Table 16/17 ... End Period, 2016 - 2025` (annual end-period values; the 2014 booklet is a scanned PDF without a text layer). It is aggregate year-end data, a different concept from the intraday bank-by-bank table, and is a possible later companion dataset. It needs its own source check and must not be merged into the bank-by-bank tables.

## 11. Rights/reuse status

The site's Terms of Use text is a modal inside the Angular bundle (the footer link is `#`; no standalone terms URL was found), and the footer reads "© Bank of Zambia. All rights reserved." Its "Permitted Use" wording: "Unless otherwise stated, the contents of this website are the property and copyright of the Bank of Zambia. The Bank grants permission for users to: Download, display or print material for personal use or use within an individual organisation and for non-commercial use only. Create links to this website. Reproduce short extracts, provided the source is stated, and any changes in the content or presentation are clearly indicated." "Further Authorisation": "For any further authorisation regarding use, including for commercial purposes, ... a written request must be made to the Bank of Zambia" (Head-Communications Division, info@boz.zm).

Reading: personal and internal non-commercial use is permitted; public display of a full table, API redistribution and commercial use are not clearly permitted and need written authorisation; the terms do not mention APIs or this dataset specifically. Silence is not permission. Recommendation: `rights_status = review_required` and **local/internal implementation only** for the first slice, consistent with the other BoZ datasets. Any public step needs the written request first.

## 12. Storage recommendation

A separate table, not `fx_rates`. `fx_rates` is one row per (pair, instant, source) with buy/mid/sell; this dataset has a different grain (date x slot x institution x scope x side), no timestamps, bank identities, a source-published average row that is not a bank, and two rate scopes. Forcing it in would need fake timestamps and a fake pair.

Two candidates: (A) one row per (snapshot, slot, institution) with four `NUMERIC` columns; (B) a long row per (snapshot, slot, institution, scope, side). **Recommend A**: it mirrors the source row, keeps the four values of one bank/slot atomic and makes spread queries single-row reads; a partial quartet is a constraint violation rather than a silent gap. Unpublished slots produce **no row** (not null rows).

**`bank_fx_snapshots`: one immutable row per encountered source node revision**

```text
observation_date            DATE   (the calendar date of the raw value at its own supplied offset, taken before the offset is lost)
source_record_id            UUID   (top-level JSON:API data[].id, the Drupal node uuid)
source_revision_id          BIGINT (drupal_internal__vid, revision-scoped)
source_internal_nid         BIGINT (drupal_internal__nid)
source_created              TIMESTAMPTZ
source_changed              TIMESTAMPTZ
source_revision_timestamp   TIMESTAMPTZ
source_title                TEXT
source_rates_datetime_raw   TEXT        (the exact JSON string received, immutable source provenance, e.g. with its +02:00)
source_rates_datetime       TIMESTAMPTZ (that string parsed to an absolute instant, for validation and querying only)
table_layout                TEXT   (detected: 'v1' | 'v2'; fail on unknown)
retrieved_at                TIMESTAMPTZ
source_url                  TEXT
rights_status               TEXT   ('review_required')
UNIQUE (source_record_id, source_revision_id)
```

PostgreSQL `TIMESTAMPTZ` stores an absolute instant and does not keep the original `+02:00` text, so the raw string is stored separately and `observation_date` is derived from it before conversion.

**Same-revision rule.** Identity is `(source_record_id, source_revision_id)`. Exact same source content (raw datetime string, title, timestamps, layout, and every row's raw names and exact rate strings) is a no-op. **Any** changed source content for the same node + revision, including a pure formatting change such as `9.9` becoming `9.9000`, is a **conflict**; stored source text is never silently overwritten. A new `vid` is a new immutable snapshot even when its numbers are identical; numeric equality is only relevant across revisions, which stay separate source snapshots. (This replaces the earlier "compared numerically" wording for the same revision; numeric comparison is for cross-revision analysis, not for deciding no-op.) A conflict that rolls back and names only date/node/revision. Never an overwrite and never a delete; older encountered revisions stay for provenance. `observation_date` is not part of the identity (a node's date could in principle be edited).

**`bank_fx_rates`: rows of one snapshot**

```text
snapshot_id, slot ('09:30'|'12:30'|'15:30', from the header labels),
institution_name_raw, institution_name, is_market_average,
interbank_buy        NUMERIC > 0,  interbank_buy_text   TEXT NOT NULL (exact source JSON string)
interbank_sell       NUMERIC > 0,  interbank_sell_text  TEXT NOT NULL
retail_buy           NUMERIC > 0,  retail_buy_text      TEXT NOT NULL
retail_sell          NUMERIC > 0,  retail_sell_text     TEXT NOT NULL
retail_source_label  TEXT          (the source's own group label for the pair: 'Retail Rates' in v1, 'BOARD RATES' in v2; never normalised)
UNIQUE (snapshot_id, slot, institution_name)
```

**Canonical snapshot for a date** (a stored-read rule, never a deletion; defensive, because multiplicity was observed but corrections were not):

1. consider snapshots whose `observation_date` equals the requested date;
2. take the node (`source_record_id`) with the newest `source_created`, matching BoZ's `sort=-created`; ties broken by the larger `source_internal_nid`, then `source_record_id`;
3. within that node use the encountered revision with the newest `source_revision_timestamp` (tie: larger `source_revision_id`);
4. the rows of that single snapshot are the answer; **never combine bank rows across snapshots** (an earlier node's `9:30` is not merged into a later node that lacks it; if the newest node is missing a slot an older node had, the slot is shown as not published in the newest node and the older snapshot remains available for provenance only).

Latest = the canonical snapshot of the newest observation date. The database enforces positive values; `buy <= sell` is **not** asserted (unproven for this source) and should be measured first.

## 13. API and importer recommendation (first slice)

Stored-only reads, no live endpoint:

- `GET /api/v1/fx/banks/latest`: **the canonical snapshot for the newest observation date currently stored in ZM Terminal.** It never contacts BoZ and does not mean "the node with the newest `source_created` that exists upstream" (the BoZ page's `sort=-created&page[limit]=1`); it is limited to dates actually imported. Within that date the canonical rule of section 12 applies (newest `source_created` node, deterministic tie-break, newest encountered revision, one snapshot, no row merging). The difference from the BoZ page is deliberate. Response: published slots, banks, market average, `retail_source_label`, provenance, rights.
- `GET /api/v1/fx/banks/history?date=YYYY-MM-DD` (canonical snapshot for one exact stored date; 404 or empty for none, no fallback).
- Importer: manual job (`python -m app.jobs.import_bank_fx --date YYYY-MM-DD [--dry-run]`), one date per run, no scheduler, no backfill loop. It requests the **exact** date with a page limit above the observed maximum and **fails if `links.next` is present**; it imports every node returned (each is an immutable snapshot), then the canonical rule above picks what is displayed.
- **Missing-date rule:** the source returns `"data": []` for a date with no publication (verified for a Saturday). The importer reports `no publication for requested date`, imports nothing and never falls back to an earlier date. Weekend roll-back, if wanted, is a separate frontend convenience and must show the actual date it resolved to.
- Decimals serialise as strings.

## 14. UI recommendation

Commands `BANK FX` and `FX BANKS`; a dashboard card only if it can show a safe headline (for example the observation date and bank count, never a made-up rate). View: slot selector (09:30 / 12:30 / 15:30), a bank table with interbank buy/sell and retail buy/sell, the source-published market-average row shown separately, a plain explanation, source/provenance and a rights notice. Labels: show "Retail (BoZ: <retail_source_label>)" using the label returned by storage/the API, so history from the older layout reads "Retail (BoZ: Retail Rates)" and newer data "Retail (BoZ: BOARD RATES)". Never hard-code or normalise the wording; "retail" stays the application-level concept.

## 15. Derived metrics and cautions

Approved for the first slice (all simple ZMW differences, computed from stored values and labelled as derived, never attributed to BoZ):

- Retail spread = retail sell - retail buy; interbank spread = interbank sell - interbank buy.
- Difference from the BoZ-published market average, for retail buy and retail sell separately.
- Highest published retail buy (best displayed rate for **selling** USD) and lowest published retail sell (best displayed rate for **buying** USD), with a range across banks.

Cautions: always say "published/displayed rate", never "best rate you will get". Displayed rates ignore fees, negotiation, transaction size, customer segment and branch conditions. Do not use the market-average row in best/worst rankings, and do not compute a national or official average. No percentage spreads yet. Do not mix this table with `FX USDZMW`.

## 16. Testing strategy

Offline, small synthetic fixtures only (never real rates); no live network in tests.

- **Rate cell grammar** `^[0-9]+\.[0-9]{1,4}$` with `Decimal > 0`. Valid: `5.1234`, `25.1234`, `105.1234`, `0.0001`, `9.9`, `9.90`, `9.900`. Invalid: `0.0000`, `0.0`, `-1.0000`, `+1.0000`, `1`, `1.`, `.5`, `1.00000`, `1e1`, `NaN`, `Infinity`, ` 1.0000`, `1.0000 `, `1,000.0000`, a JSON number, `null`.
- Blank handling: a fully blank slot gives no row; a partial quartet or a non-numeric cell rejects the node.
- Exactly three source slots located by the header labels; both layouts v1 and v2 accepted; an unknown header text or layout fails loudly; header structure (three header rows, 13 cells).
- Bank name: whitespace normalisation, raw preservation, a duplicate normalised name rejected, blank name rejected.
- Market average: detected case-insensitively, flagged, never ranked as a bank, a missing one is a decision (reject first).
- Complete four-rate quartet per published slot.
- Provenance: raw `commercial_bank_rates_date` retained exactly (including its offset text); the parsed timestamp validated independently; `observation_date` derived at the supplied offset; every exact rate string retained alongside its `NUMERIC`.
- Persistence on a disposable database: identical node+revision (identical raw strings) is a no-op; the same node+revision with altered raw source content (including a formatting-only change such as `9.9` versus `9.9000`) conflicts with nothing written; a different revision with numerically identical values is a separate snapshot; a new revision of the same node is a new snapshot; v1 data returns `Retail Rates` and v2 data `BOARD RATES` as `retail_source_label`; stored `/latest` selects the newest imported observation date, not the globally greatest `source_created`; two nodes for one date both stored, canonical selection per the rule (newest created node, newest revision, tie-breaks), never merged across snapshots.
- Importer: `"data": []` reports no publication and writes nothing; `links.next` present fails; no previous-day fallback.

## 17. Implementation sequence

1. (Done) Source discovery and bounded historical-contract check (this document).
2. Strict adapter/parser with fixtures for both layouts, then the storage migration (two tables above).
3. Manual importer (exact date, no fallback) and conflict-aware store.
4. Stored-only latest/history read API.
5. Frontend commands `BANK FX` / `FX BANKS`, view and tests.
6. Annual end-period companion dataset only after its own source check.

## 18. Open questions

- Can a published node be corrected in place (a new `vid` with different numbers)? `changed` exceeds `created` everywhere, but revision content was not inspected.
- Is `BOARD RATES` the same as bank "retail" rates in practice? The source header says `BOARD RATES` (v2) and `Retail Rates` (v1); keep both labels.
- How does BoZ compute the market average (simple or weighted, which banks)? Not stated; leave undocumented.
- Are there records earlier than 2026-01-28 by observation date but created later? Not ruled out.
- What is the endpoint's maximum page size?
- Written permission from BoZ for anything beyond local/internal use.
