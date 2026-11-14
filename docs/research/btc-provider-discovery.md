# BTC/USD and BTC/ZMW provider discovery

Decision record, 2026-11-14. **Research only.** No BTC adapter, table, route, command or UI exists or was added; nothing was persisted. Scratch downloads (Bitstamp candles, provider pages) were kept outside the repository. No account, API key or credential was created or used: every Bitstamp, Kraken, Coinbase and CoinGecko request was an unauthenticated public GET with the client name `zm-terminal-research/1.0`.

Conventions: **Fact** = read or measured on 2026-11-14. **Interpretation** = our reading. **Unresolved** = needs the provider's written answer. Status vocabulary: `acceptable for local research` (an engineering/source-governance classification, not legal advice or a formal licensing determination), `review_required`, `rejected_without_licence`, `rejected_without_written_consent`, `not suitable`.

## 1. Outcome

**APPROVED FOR LOCAL IMPLEMENTATION — PUBLIC RIGHTS PENDING**, with one stated gap: the Bitstamp Terms of Use page could not be read (section 3.5), so the user should read it in a browser before the first BTC code is written.

- Preferred BTC/USD source: **Bitstamp BTC/USD spot market** (live ticker `last`; completed daily OHLCV history from 2011-08-18). Public endpoints, no authentication.
- Public display, public API redistribution and commercial use are **`review_required`**: Bitstamp's API page directs commercial users to a Data License Agreement (DLA) and the text does not say what a non-commercial public site needs.
- BTC/ZMW: **derived** from Bitstamp BTC/USD and the stored Bank of Zambia USD/ZMW `mid`; no acceptable direct BTC/ZMW market source was identified.
- Coinbase is `rejected_without_written_consent` for a public deployment; CoinGecko only fits as a non-stored cross-check; Kraken is a technical backup with unverified terms.

## 2. Evaluation matrix

| Candidate | Price type | Live | History | Auth | Historical depth | Persist | Public display | API redistribution | Commercial | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| Bitstamp | One exchange's BTC/USD spot (last trade) | Ticker, no auth | OHLCV 60 s to 3 d candles | None | Daily from 2011-08-18 | `acceptable for local research` | `review_required` | `review_required` | `review_required` (DLA stated) | **PREFERRED** |
| CoinGecko | Aggregated multi-exchange VWAP | Keyless (~5 calls/min) or key | Daily/hourly | Optional key | Demo 1 y, Basic 2 y, Analyst and above from 2013 | `review_required` (terms say do not store or derive) | Paid plan + attribution | Enterprise / Executed Agreement | Paid plan | **Cross-check only; Enterprise for our architecture** |
| Kraken | One exchange's XBT/USD spot | Ticker, no auth | OHLC, 720 candles per call | None | About 720 daily candles via REST; download unverified | `review_required` | `review_required` | `review_required` | `review_required` | **Technical backup; licence unverified** |
| Coinbase | One exchange's BTC-USD spot | Ticker, no auth | Candles, 300 per call | None | Not assessed | `acceptable for local research` | `rejected_without_written_consent` | `rejected_without_written_consent` | `rejected_without_written_consent` | **REJECTED for public deployment** |
| CoinPaprika | Aggregated | API key | Daily | Key | Free 1 y daily, Starter 5 y | Not assessed | Unclear | Enterprise only | Paid plans (internal use) | **Paid fallback, not preferred** |

## 3. Bitstamp

### 3.1 Facts: ticker (`GET /api/v2/ticker/btcusd/`)
- The official docs (https://www.bitstamp.net/api/, read directly) describe each field: `last` "Last price."; `high` "Last 24 hours price high."; `low` "Last 24 hours price low."; `vwap` "Last 24 hours volume weighted average price."; `volume` "Last 24 hours volume."; `bid` "Highest buy order."; `open` "First price of the day."; `open_24` "24 hours time delta transaction price"; `timestamp` "Unix timestamp date and time."; `side` "Ticker side: 0 - buy; 1 - sell."; `market_type` SPOT or PERPETUAL.
- Live response (unauthenticated, HTTP 200, `Cache-Control: max-age=1`): `last` 67185.23, `timestamp` 1794656512 (2026-11-14 11:41:52 UTC), `market_type` `SPOT`, prices to 2 decimals, volume to 8 decimals, no `pair` field on this single-market response.
- **Interpretation:** the right quote field is `last` (the latest executed BTC/USD trade). `bid`/`ask` (an order-book quote), `vwap`, `open`, `high`, `low` and `volume` mean different things and must not be substituted.
- **Behaviour to remember:** an unknown market in the ticker path (`/ticker/btczmw/`) returned HTTP 200 with a 79 KB array of all 272 markets, not an error; an uppercase `BTCUSD` also worked. A future adapter must therefore check that the response is an object whose `market`/pair is `BTC/USD` and `market_type` is `SPOT`, and must not trust the HTTP status alone.

### 3.2 Facts: OHLC (`GET /api/v2/ohlc/btcusd/`)
Docs and measurements agree:
- Parameters: `step` (a fixed set of seconds from 60 up to 259200, including 86400), `limit` (1 to 1000; 1001 gave HTTP 400 `validation-error`), optional `start`, `end`, `exclude_current_candle` (default false; "results won't include current (open) candle"). An invalid `step` gave HTTP 400; an unknown pair gave HTTP 404 with an empty body.
- Docs: "If both start and end timestamps are posted, end timestamp will be used." Measured: with `start` and `end` and `limit=1000`, the API returned the 1000 candles *ending at* `end` and ignored `start` as a lower bound. **Use `start` + `limit` only for forward pagination** and filter dates on our side.
- Each candle: `timestamp`, `open`, `high`, `low`, `close`, `volume` as strings (prices 2 decimals, volume 8 decimals).
- **Daily candles (`step=86400`) are UTC calendar days**: all 5,553 timestamps measured were exactly 00:00:00 UTC.
- Earliest daily candle: **2011-08-18** (`start=2011-08-17` returns it first; `start=2011-08-16` returns an empty list). Latest completed candle with `exclude_current_candle=true`: **2026-11-13**.
- Full forward pagination (6 requests at 1.5 s spacing, `start` + `limit=1000`, `exclude_current_candle=true`, from 2011-09-01): 5,553 candles to 2026-11-13, strictly ascending, no duplicates, **no gaps** in the daily sequence (candles 2011-08-18 to 2011-08-30 were not gap-checked), low ≤ open/close ≤ high on every candle.
- **Empty days are synthesized:** 28 candles (all in 2011 and 2015) have volume 0.00000000; 27 of those are flat at the previous close (open = high = low = close). So a candle with zero volume is a carry-forward, not a trade. A future importer must store volume as published and must not treat a zero-volume candle as an observed trade.
- Rate limits stated on the page: "400 requests per second" and "10,000 requests per 10 minutes". Total Bitstamp research traffic: a few dozen small requests, well inside the stated limits.
- No authentication was requested at any point; no redirect or access gate was met. (The Bitstamp page `www.bitstamp.net/api/` itself was fetched once with the same client name.)

### 3.3 Live versus history
- Measured at one instant: ticker `last` 67218.65 equals the `close` of the current (open) UTC-day candle 67218.65; the ticker's `open` (68227.80) equals the candle `open`. The ticker's `high`, `low` and `volume` are *rolling 24 h* values and differ from the UTC-day candle (for example volume 1158.4 against 434.4).
- **Interpretation:** a completed daily `close` is the last trade of that UTC day (the in-progress candle's close tracks `last`). It is not a daily average, not a VWAP and not a midpoint, and the UI must not describe it as one. `last` is the live value; the stored daily series is OHLCV.
- Bitstamp is **one exchange's** BTC/USD market. It must be labelled "Bitstamp BTC/USD spot", never "the Bitcoin price" or a global reference rate.

### 3.4 Direct BTC/ZMW
- None of Bitstamp's 272 listed markets contains ZMW (BTC pairs seen: USD, EUR, GBP, USDC, USDT, RLUSD, EURC, EURCV and a USD perpetual). **No acceptable direct BTC/ZMW market source was identified.** This is a statement about the sources examined, not a claim that no BTC/ZMW market exists anywhere. P2P adverts, OTC offers, mobile-money merchant quotes and informal exchange quotes were not used and are not benchmarks.

### 3.5 Licensing
Facts (verbatim from the API page, "Commercial Use of Bitstamp's Exchange Data"):
- "Companies seeking to utilize Bitstamp's exchange data for their own commercial purposes are directed to contact partners@bitstamp.net to receive and sign a commercial use Data License Agreement."
- "Bitstamp allows the incorporation and redistribution of our exchange data for commercial purposes. This includes the right to create ratios, calculations, new original works, statistics, and similar, based on the exchange data."
- The Terms of Use (https://www.bitstamp.net/terms-of-use/) and `api-terms` pages returned an empty or script-only shell to a plain request (212 bytes) and a fetch tool returned zero characters, and a web search did not return Bitstamp's own terms text. **They were not read.** No attribution requirement is stated on the API page.

Interpretation and unresolved points:
- The DLA is stated for companies using the data "for their own commercial purposes". It is not stated whether a public, non-commercial research site needs one, whether persisting history needs one, or whether the second sentence ("allows… redistribution… for commercial purposes") is unconditional or the grant that the DLA delivers. The two sentences read together suggest the DLA is the route to commercial redistribution and derived works such as BTC/ZMW.
- Nothing read restricts private/local research use of the public endpoints.

| Use | Status | Reason |
|---|---|---|
| Local/private research | `acceptable for local research` | Public unauthenticated endpoints; no restriction found. Caveat: Terms of Use unread. |
| Persisting normalised BTC/USD observations locally | `acceptable for local research` | Same; no raw-payload storage needed. |
| Public non-commercial display | `review_required` | DLA scope for non-commercial public use is not stated. |
| Public API redistribution of normalised observations | `review_required` | Redistribution is mentioned only under commercial use. |
| Commercial display | `review_required` | DLA stated as required. |
| Commercial API redistribution | `review_required` | DLA stated as required. |
| Derived BTC/ZMW (local) | `acceptable for local research` | Calculations are expressly contemplated. |
| Derived BTC/ZMW (public/commercial) | `review_required` | Calculations are contemplated under the commercial wording; public status unclear. |

The DLA is **required for commercial use** (stated), **unclear** for any other public redistribution and for persistence.

## 4. Other candidates

### 4.1 CoinGecko (aggregated market, not one exchange)
- Methodology (https://www.coingecko.com/en/methodology, per the fetch report): an initial ticker set of up to the top 600 tickers by volume for a coin, outlier tickers removed (median absolute deviation for three or more tickers), then the VWAP of the remaining tickers is the aggregated price. Bitcoin id: `bitcoin`. `/simple/price` supports `include_last_updated_at` (UNIX seconds). Endpoints are cached about 1 to 5 minutes (Demo "from 60 sec", Basic "from 10 sec", paid "Real-time").
- Historical daily depth (pricing page): Demo 1 year, Basic 2 years, Analyst/Lite/Pro/Enterprise from 2013. The keyless public API is still documented (about 5 calls/minute, "not recommended for production").
- Terms (version 5 Sept 2025, per the fetch report): "We do not encourage caching or storage of Data" (6.1); "you are not allowed to duplicate, reproduce, copy, store, derive from or translate any Data" (6.2); "you are not permitted to sell, rent, lease, sub-license, re-distribute or syndicate access to the CoinGecko API or part thereof" except under an "Executed Agreement"; a paid commercial plan requires visible attribution ("Data provided by CoinGecko" on the pricing FAQ; "Powered by CoinGecko" in the terms), and redistribution or white-labelling "requires a custom license".
- Interpretation: ZM Terminal's design (persist normalised history, expose it through its own API) collides with the storage and redistribution wording; a standard paid plan would at most cover displaying live values inside the product. **Enterprise/custom licence only for our intended architecture.** Its aggregate price is a different thing from an exchange BTC/USD price.
- One keyless request for the plausibility check returned `{"bitcoin":{"usd":67163,"last_updated_at":1794656781}}` (whole dollars).

### 4.2 Kraken (one exchange, XBT/USD)
- Public REST needs no account. Ticker (read from docs.kraken.com): `c` "Last trade closed", `o` "Today's opening price", "Today's prices start at midnight UTC"; pair `XBTUSD` returns the key `XXBTZUSD`. OHLC: "Returns up to 720 of the most recent entries (older data cannot be retrieved, regardless of the value of `since`)" with the last row the current, uncommitted candle. So REST daily history is about two years, not the full history.
- Licensing: the Kraken API support page lists "Market data licensing | marketdata@kraken.com". I could not find or verify a clause requiring prior permission for non-personal commercial use of public market data (the fetch tool's summary of Kraken's global terms mentions restrictions on making "Our Content" available to third parties; that wording was not seen verbatim). The downloadable OHLCVT history and its terms were not found.
- Decision: `review_required` for every public/commercial use; technical backup only.

### 4.3 Coinbase (one exchange, BTC-USD)
- Public `GET /products/BTC-USD/ticker` and `/candles` need no authentication; candles are capped at 300 per request (docs.cdp.coinbase.com, per the fetch report), and a live ticker request succeeded.
- **Market Data Terms of Use (https://www.coinbase.com/legal/market_data, "Last updated: August 7, 2026", read directly):** "Your use of Market Data is exclusively for you or your entity's personal or research purposes and may not be used to build an application intended for use by end users other than for you or your officers/employees." And: "Absent prior express written consent from Coinbase, you may not: … Redistribute, display, or disseminate the Market Data—or any data, charts, analytics, research, or other works … derived from the Market Data (“Derived Works”) — to any third party outside of your organization". It also bars indexes, fixings, benchmarks, generic or fair value prices.
- Decision: `rejected_without_written_consent` for a public ZM Terminal (and for its public API); `acceptable for local research` only.

### 4.4 CoinPaprika
- From the official docs (docs.coinpaprika.com plans page and FAQ, read directly): Free = "Personal" usage; Starter, Pro, Business, Ultimate = "Commercial"; **Redistribution** is "✅" only for Enterprise. FAQ: commercial use "inside the customer's company in all paid packages. However, only the Enterprise package allows data to be redistributed or shared, which requires a dedicated contract." Daily history: Free 1 year, Starter 5 years, Pro and above full. The pricing page itself returned 403 to the fetch tool and was not read.
- Whether showing values to the public counts as redistribution was not stated in the official text read. Decision: paid fallback, not preferred; `review_required`.

### 4.5 OpenBB is not a shortcut
- The repository's OpenBB image installs `openbb-platform-api`, `openbb-cboe`, `openbb-oecd` and `openbb-fred` only: it exposes no Bitcoin price. The V5 provider table on docs.openbb.co (per the fetch report) has no crypto price extension; the only crypto-related package is `openbb-deribit` (derivatives), and `openbb-fmp`, `openbb-tiingo` and `openbb-yfinance` are listed as removed in V5. OpenBB v4 docs were not checked. Whatever OpenBB offered, the underlying provider's rights would govern. Not used.

## 5. Plausibility cross-check
At about 11:47 UTC on 2026-11-14 (single requests, not averaged, not used to choose a value): Bitstamp `last` 67166.18, Kraken `c` 67166.80, Coinbase ticker `price` 67169.42, CoinGecko (aggregate, whole dollars, about 66 s old) 67163. They agree within roughly 0.01% at that instant. Exchange prices differ slightly from each other and from the aggregate by construction; the comparison shows plausibility only, does not make the methodologies equivalent and says nothing about licence status.

## 6. Semantics and design (not implemented)

### 6.1 BTC/USD
- Live: Bitstamp ticker `last`, with the ticker `timestamp` as the observation time. Label "Bitstamp BTC/USD spot". Do not use `bid`/`ask` midpoint, VWAP, index or mark price.
- History: **full daily OHLCV** (option B) rather than close only. Reasons: one request already carries all fields; about 5,550 rows for the whole history; charts and later analytics need no reconstruction; no extra licensing difference. Cost is a wider table and a note that zero-volume candles are carry-forwards. Provisional fields (not a final schema): market `BTC/USD`, base, quote, provider `bitstamp`, interval `1d`, `period_start`/`period_end` (UTC day), open, high, low, close, `volume_btc`, source URL, `retrieved_at`. Persist only candles returned with `exclude_current_candle=true` and also require `period_end` to be in the past on our clock. Never forward-fill, interpolate or synthesise candles; store the provider's zero-volume candles as published.
- Values are exact decimal strings (2 and 8 places); store as `Decimal`, no quantisation; rounding is presentation only.
- Storage: a dedicated BTC/USD daily table of the same shape family as `global_oil_prices`; no raw JSON archive (normalised data plus provenance suffice). Do not persist ticker snapshots (a tick archive would build an unlicensed history). Follow the oil rule that a changed stored candle is a conflict, never a silent overwrite.

### 6.2 Live BTC/ZMW
`btc_zmw = btc_usd_last × boz_usdzmw_mid` in `Decimal`, no float, computed on request and not persisted. Return both components with their own source and observation time (BTC ticker timestamp; BoZ `observed_at`), the derived time, and each component's age. Notice: "Derived from an external BTC/USD market price and Bank of Zambia USD/ZMW. It is an indicative conversion, not an executable BTC/ZMW market quote." It is not an exchange-traded price, dealer quote, P2P price, mobile-money rate or Bitstamp BTC/ZMW market.

Staleness (measured from the stored data): BoZ USD/ZMW has 492 stored observations on 167 Lusaka dates since 2026-01-22, normally three per business day at 09:30, 12:30 and 15:30 CAT (07:30, 10:30, 13:30 UTC); the newest is 2026-11-13 13:30 UTC, so on a Saturday the FX leg is already about a day old while Bitcoin trades continuously. The existing FX flag (`FX_STALE_AFTER = 96 h`) will not mark an ordinary weekend stale (Friday 15:30 CAT to Monday 09:30 CAT is about 66 h). Recommendations:
- BTC/USD live: its own flag from the ticker timestamp, for example stale after a few minutes (threshold to be chosen at implementation).
- BTC/ZMW live: **stale if either input is stale**, never "fresh" because Bitcoin just updated; always show the FX age in words ("USD/ZMW from Fri 13 Nov 15:30 CAT") even when it is not flagged.
- Never invent an FX rate: no stored BoZ observation means no BTC/ZMW.

### 6.3 Historical BTC/ZMW
- Option A (daily derived on overlapping stored BoZ dates): `UTC-day close × arithmetic mean of that Lusaka date's stored BoZ mid observations`, no weekend or holiday fill. Option B (monthly, `OIL ZM` style) discards the daily Bitcoin information the stored OHLCV gives. Option C (live only first) is the smallest change.
- Recommendation: ship **daily BTC/USD OHLCV and live BTC/ZMW first**, then Option A as a later derive-on-request series on overlapping dates. This is reasoned, not merely adopted: it keeps the first slice small and avoids a derived series whose calendar mismatch needs its own review.
- Date alignment, stated exactly: Bitstamp daily candles are UTC days (00:00 to 24:00 UTC = 02:00 to 02:00 CAT). All three BoZ observation times fall on the same calendar date in both UTC and Lusaka, so a BoZ date maps unambiguously to the same-dated candle. The mismatch is in time of day: the candle closes at 24:00 UTC, about 10.5 hours after the last BoZ observation (13:30 UTC). That is acceptable if disclosed ("FX from the same calendar date's BoZ business hours; Bitcoin close at 00:00 UTC"). There is no better alignment without fabricating observations.
- **BoZ limit:** stored USD/ZMW history begins 2026-01-22, so derived BTC/ZMW cannot honestly begin earlier. No alternative FX source should be added for this.

### 6.4 Errors (future)
BTC/USD live: provider timeout 504, invalid or unavailable provider response 502 (including an HTTP 200 whose body is not the expected `BTC/USD` object). Stored history: 503 on database failure, no upstream request. Derived BTC/ZMW: distinguish provider failure (502/504) from missing FX (no stored BoZ observation); never return BTC/USD labelled as BTC/ZMW.

### 6.5 Secrets and raw data
Bitstamp public endpoints need no key (verified). If a key is ever needed it belongs in a backend-only environment variable, never in the frontend, repository or logs. Do not store raw upstream payloads.

### 6.6 Provider policy (recommended future values, not edited here)
For `bitstamp` BTC/USD: `authentication` none; `normalized_storage_allowed` true for local use; `raw_response_storage` disabled; `attribution_required` true (cautious default; Bitstamp states no rule); `redistribution` `review_required`; `commercial_use` `review_required`; required provenance source, market, source URL, unit; timestamps `observed_at`/period bounds and `retrieved_at`. Keep local and public rights separate so a later DLA changes policy, not code. Request guidance: one ticker request per user action with a short server-side cache, no polling; history by manual backfill with `start` + `limit=1000` spaced at least 1.5 s.

### 6.7 Commands and UI (design only)
- Smallest set: `BTC` (and `BTC USD`) for Bitstamp BTC/USD, and `BTC ZMW` for the derived view. Do not add `BTC ZM` at first: elsewhere `ZM` marks a Zambian source or view, and here it could be read as a Zambian Bitcoin market.
- BTC/USD heading `BITCOIN · BTC/USD` with sublabel `BITSTAMP SPOT`. Derived view `BITCOIN · INDICATIVE BTC/ZMW` with a `DERIVED` badge showing `BTC/USD × USD/ZMW = BTC/ZMW`, each component with its source, time and age, and the notice above.

## 7. Email draft: Bitstamp (NOT SENT)

To: `partners@bitstamp.net`
Subject: Data licensing question: public BTC/USD data in a research application

> Hello,
>
> I am building a research application (ZM Terminal) that would use Bitstamp's public BTC/USD market data. It would not proxy the Bitstamp API, resell data or use Bitstamp credentials. Before launching publicly I would like to confirm what rights are needed. Could you please tell me whether:
> 1. use of the public `BTC/USD` ticker is covered without an agreement;
> 2. storing normalised ticker values and daily OHLC candles (`/api/v2/ohlc/btcusd/`, `step=86400`) is allowed, and whether full daily OHLCV history may be persisted;
> 3. displaying normalised BTC/USD prices and charts in a public research application is allowed, and on what terms for non-commercial and commercial use;
> 4. exposing those normalised observations through the application's own API is allowed;
> 5. deriving and displaying an indicative BTC/ZMW value from Bitstamp BTC/USD and the Bank of Zambia USD/ZMW rate is allowed;
> 6. a commercial Data License Agreement is required before public launch, and for which of the above;
> 7. any attribution is required.
>
> Thank you,
> [name]

Status: drafted here only; not sent. The sender name is left blank.

## 8. Final recommendation

### Preferred provider
Bitstamp BTC/USD spot (ticker `last`, completed daily OHLCV).

### Backup provider
Kraken XBT/USD (same single-exchange spot semantics, no authentication, but only about 720 daily candles by REST and unverified licence). CoinGecko is a cross-check only (aggregate; terms conflict with storing and re-serving data).

### Rejected providers
Coinbase for public deployment (`rejected_without_written_consent`); CoinPaprika as preferred (redistribution Enterprise only); anything P2P, OTC or scraped.

### Licensing status
Local research `acceptable for local research`; public display, API, commercial and public derived BTC/ZMW `review_required`.

### Blocking questions
1. Does a public non-commercial application need a Bitstamp DLA, and does it cover persisted history and a derived BTC/ZMW? (`partners@bitstamp.net`)
2. What do Bitstamp's Terms of Use say (unread here)? Please read them in a browser before development starts.

### Recommended next action
Read the Bitstamp Terms of Use, then build `BTC` (BTC/USD live and daily OHLCV, local use only) while the Bitstamp email is reviewed and sent by the user. Do not enable a public deployment until written confirmation or a DLA exists. Gold remains blocked on the World Bank response; this does not change it.

**Final state: APPROVED FOR LOCAL IMPLEMENTATION — PUBLIC RIGHTS PENDING**
