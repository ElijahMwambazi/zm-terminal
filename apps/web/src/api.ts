const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type SystemStatus = {
  api: boolean;
  database: boolean;
  openbb: boolean;
};

export async function getSystemStatus(): Promise<SystemStatus> {
  const response = await fetch(`${API_URL}/api/v1/system`);
  if (!response.ok) throw new Error("Unable to load system status");
  return response.json();
}

export type FxQuote = {
  symbol: "USDZMW";
  buy: string;
  mid: string;
  sell: string;
  observed_at: string;
  retrieved_at: string;
  source: "boz";
  source_url: string;
  stale: boolean;
};

export type FxHistoryPoint = {
  observed_at: string;
  buy: string;
  mid: string;
  sell: string;
  source: "boz";
};

export const HISTORY_RANGES = ["1D", "1W", "1M", "3M", "1Y", "ALL"] as const;
export type HistoryRange = (typeof HISTORY_RANGES)[number];

/** "upstream": Bank of Zambia failed (502/504). "api": our API is unreachable or failed. */
export class ApiError extends Error {
  constructor(readonly kind: "upstream" | "api", message: string) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 502 || response.status === 504) {
    throw new ApiError("upstream", "Bank of Zambia data is currently unavailable");
  }
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export const getUsdZmwQuote = () => getJson<FxQuote>("/api/v1/fx/USDZMW");

export async function getUsdZmwHistory(range: HistoryRange): Promise<FxHistoryPoint[]> {
  const body = await getJson<{ observations: FxHistoryPoint[] }>(
    `/api/v1/fx/USDZMW/history?range=${range}`,
  );
  return body.observations;
}

export const TENORS = ["91D", "182D", "273D", "364D"] as const;
export type Tenor = (typeof TENORS)[number];

/** Rates are percent per annum, decimal strings exactly as BoZ publishes them. */
export type TreasuryBill = {
  tenor: Tenor;
  auction_date: string; // YYYY-MM-DD
  tender_number: string;
  isin: string;
  discount_rate: string;
  yield_rate: string;
  source: "boz";
  source_url: string;
  retrieved_at: string;
  stale: boolean;
  /** Rejected tenders dated after this auction; non-empty means this value is a fallback. */
  newer_rejected_tenders: string[];
};

export type TreasuryBillsLatest = {
  tenors: TreasuryBill[];
  missing_tenors: Tenor[];
  skipped_tenders: string[];
};

export const getTreasuryBills = () => getJson<TreasuryBillsLatest>("/api/v1/treasury-bills/latest");

export const BOND_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "15Y"] as const;
export type BondTenor = (typeof BOND_TENORS)[number];

/** Rates are percent per annum, decimal strings exactly as BoZ publishes them. */
export type GovernmentBond = {
  tenor: BondTenor;
  auction_date: string; // YYYY-MM-DD
  tender_number: string;
  isin: string;
  coupon_rate: string;
  yield_rate: string;
  source: "boz";
  source_url: string;
  retrieved_at: string;
  /** Tenders with a newer rejected record that could have carried THIS tenor; non-empty = fallback. */
  newer_rejected_tenders: string[];
};

export type GovernmentBondsLatest = {
  tenors: GovernmentBond[];
  missing_tenors: BondTenor[];
  /** Missing tenors that a rejected source record could have carried. */
  unverified_tenors: BondTenor[];
  skipped_tenders: string[];
};

export const getGovernmentBonds = () => getJson<GovernmentBondsLatest>("/api/v1/government-bonds/latest");

export const TBILL_RANGES = ["1M", "3M", "6M", "1Y", "ALL"] as const;
export type TbillRange = (typeof TBILL_RANGES)[number];
export const BOND_RANGES = ["3M", "6M", "1Y", "ALL"] as const;
export type BondRange = (typeof BOND_RANGES)[number];

/** A stored auction observation; auctions that did not happen are simply absent. */
export type AuctionPoint = {
  tenor: string;
  auction_date: string; // YYYY-MM-DD
  tender_number: string;
  isin: string;
  yield_rate: string;
  discount_rate?: string;
  coupon_rate?: string;
};

async function getHistory(path: string): Promise<AuctionPoint[]> {
  try {
    return (await getJson<{ observations: AuctionPoint[] }>(path)).observations;
  } catch (error) {
    if (error instanceof ApiError && error.message.includes("(503)")) {
      throw new ApiError("api", "Stored history is unavailable (database)");
    }
    throw error;
  }
}

export const getTbillHistory = (tenor: Tenor, range: TbillRange) =>
  getHistory(`/api/v1/treasury-bills/history?tenor=${tenor}&range=${range}`);
export const getBondHistory = (tenor: BondTenor, range: BondRange) =>
  getHistory(`/api/v1/government-bonds/history?tenor=${tenor}&range=${range}`);

export const FUEL_PRODUCTS = ["PETROL", "DIESEL", "KEROSENE", "JET_A1", "LSGO"] as const;
export type FuelProduct = (typeof FUEL_PRODUCTS)[number];
export const FUEL_LABELS: Record<FuelProduct, string> = {
  PETROL: "PETROL", DIESEL: "DIESEL", KEROSENE: "KEROSENE", JET_A1: "JET A-1", LSGO: "LSGO",
};
export const FUEL_RANGES = ["1Y", "3Y", "5Y", "ALL"] as const;
export type FuelRange = (typeof FUEL_RANGES)[number];

/** ZMW per litre exactly as ERB publishes it. effective_date is the first day of the pricing month. */
export type FuelPrice = {
  product: FuelProduct;
  effective_date: string;
  price_zmw: string;
  currency: "ZMW";
  unit: "litre";
  price_type: "retail";
  source: "erb";
  source_url: string;
  retrieved_at: string;
};

export type FuelLatest = { prices: FuelPrice[]; missing_products: FuelProduct[] };
export type FuelHistoryPoint = { product: FuelProduct; effective_date: string; price_zmw: string };

export const getFuelLatest = () => getJson<FuelLatest>("/api/v1/fuel/latest");

export async function getFuelHistory(product: FuelProduct, range: FuelRange): Promise<FuelHistoryPoint[]> {
  try {
    return (await getJson<{ observations: FuelHistoryPoint[] }>(
      `/api/v1/fuel/history?product=${product}&range=${range}&type=retail`)).observations;
  } catch (error) {
    if (error instanceof ApiError && error.message.includes("(503)")) {
      throw new ApiError("api", "Stored fuel prices are unavailable (database)");
    }
    throw error;
  }
}

export type BuildUpType = "pump" | "wholesale";
export type BuildUpComponent = {
  code: string;
  label: string;
  /** null = ERB printed "-" (not published); never zero. */
  amount: string | null;
  currency: string | null;
  unit: string | null;
  note: string | null;
};
export type FuelBuildUp = {
  product: FuelProduct;
  effective_date: string;
  publication_month: string;
  build_up_type: BuildUpType;
  components: BuildUpComponent[];
  source_url: string;
  retrieved_at: string;
};

export async function getFuelBuildUp(product: FuelProduct, type: BuildUpType): Promise<FuelBuildUp | null> {
  try {
    return await getJson<FuelBuildUp>(`/api/v1/fuel/build-up?product=${product}&type=${type}`);
  } catch (error) {
    if (error instanceof ApiError && error.message.includes("(404)")) return null;
    if (error instanceof ApiError && error.message.includes("(503)")) {
      throw new ApiError("api", "Stored fuel prices are unavailable (database)");
    }
    throw error;
  }
}


export const AGRICULTURE_COMMODITIES = ["MAIZE", "SOYA_BEANS"] as const;
export type AgricultureCommodity = (typeof AGRICULTURE_COMMODITIES)[number];
export const AGRICULTURE_LABELS: Record<AgricultureCommodity, string> = {
  MAIZE: "MAIZE", SOYA_BEANS: "SOYA BEANS",
};
export const AGRICULTURE_RANGES = ["1Y", "3Y", "ALL"] as const;
export type AgricultureRange = (typeof AGRICULTURE_RANGES)[number];

export type AgriculturePrice = {
  commodity: AgricultureCommodity;
  market_type: "national_average";
  provider: "moa";
  period_start: string;
  period_end: string;
  publication_date: string | null;
  publication_date_source: string | null;
  grade: string | null;
  variety: string | null;
  location_scope: "national";
  location: string | null;
  price_basis: "indicative_average";
  source_price: string;
  source_currency: "ZMW";
  source_unit: "kg";
  normalized_zmw_per_kg: string;
  source: string;
  source_url: string;
  source_period_label: string;
  retrieved_at: string;
};

export type AgricultureLatest = {
  prices: AgriculturePrice[];
  missing_commodities: AgricultureCommodity[];
};

export type AgricultureHistoryPoint = {
  commodity: AgricultureCommodity;
  period_start: string;
  period_end: string;
  source_price: string;
  normalized_zmw_per_kg: string;
  source_unit: "kg";
  price_basis: "indicative_average";
  source: string;
  source_url: string;
};

export const getAgricultureLatest = () => getJson<AgricultureLatest>("/api/v1/agriculture/latest");

export async function getAgricultureHistory(
  commodity: AgricultureCommodity, range: AgricultureRange,
): Promise<AgricultureHistoryPoint[]> {
  try {
    return (await getJson<{ observations: AgricultureHistoryPoint[] }>(
      `/api/v1/agriculture/history?commodity=${commodity}&range=${range}`,
    )).observations;
  } catch (error) {
    if (error instanceof ApiError && error.message.includes("(503)")) {
      throw new ApiError("api", "Stored agriculture prices are unavailable (database)");
    }
    throw error;
  }
}

export const OIL_BENCHMARKS = ["BRENT", "WTI"] as const;
export type OilBenchmark = (typeof OIL_BENCHMARKS)[number];
export const OIL_RANGES = ["1Y", "3Y", "5Y", "ALL"] as const;
export type OilRange = (typeof OIL_RANGES)[number];

/** Global crude-oil benchmark: one calendar-month average in USD per barrel, exactly as the World Bank publishes it.
 * Not a daily or live price; period_start/period_end are the first and last day of the month. */
export type OilObservation = {
  benchmark: OilBenchmark;
  name: string;
  description: string;
  period_start: string;
  period_end: string;
  value: string;
  currency: "USD";
  unit: "barrel";
  source: "world_bank";
  price_basis: "monthly_average";
  source_series: string;
  source_url: string; // the exact release workbook (machine provenance)
  source_page_url: string; // stable human-facing page
  publication_date: string;
  retrieved_at: string;
  stale: boolean;
  /** Backend heuristic (calendar months behind the current month); the UI displays it and never hardcodes it. */
  stale_after_months: number;
};
export type OilLatest = { observations: OilObservation[]; missing_benchmarks: OilBenchmark[] };
export type OilPoint = { benchmark: OilBenchmark; period_start: string; period_end: string; value: string };

/** Oil is read from storage only, so the shared getJson (which names the Bank of Zambia) is not used. */
async function getOilJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored oil benchmarks are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export const getOilLatest = () => getOilJson<OilLatest>("/api/v1/oil/latest");

export async function getOilHistory(benchmark: OilBenchmark, range: OilRange): Promise<OilPoint[]> {
  return (await getOilJson<{ observations: OilPoint[] }>(`/api/v1/oil/history?benchmark=${benchmark}&range=${range}`)).observations;
}

export const OIL_ZM_RANGES = OIL_RANGES;

/** OIL ZM: a DERIVED monthly ZM Terminal indicator (Brent USD/bbl x BoZ USD/ZMW monthly mid average), never an official
 * series and not a Zambia pump price. The API does all arithmetic; the UI only displays and rounds for presentation. */
export type OilZmPoint = {
  classification: "derived";
  derived: true;
  period_start: string;
  period_end: string;
  currency: "ZMW";
  unit: "barrel";
  zmw_per_bbl: string;
  brent_usd_per_bbl: string;
  usd_zmw_observed_days_avg: string;
  fx_day_count: number;
  fx_observation_count: number;
  /** partial_source_window: stored BoZ history begins during this month. observed_days: uses whatever stored days exist; asserts no completeness. */
  fx_coverage: "partial_source_window" | "observed_days";
};
export type OilZmMonth = OilZmPoint & {
  methodology_id: string;
  methodology: string;
  notice: string;
  fx_first_date: string;
  fx_last_date: string;
  fx_first_observation: string;
  fx_last_observation: string;
  brent_source: "world_bank";
  brent_source_url: string;
  brent_source_page_url: string;
  brent_publication_date: string;
  fx_source: "boz";
  fx_pair: "USDZMW";
  fx_price: "mid";
  fx_source_url: string;
  stale: boolean;
  stale_after_months: number;
};
export type OilZmHistory = { range: OilRange; count: number; brent_months_without_fx: number; observations: OilZmPoint[] };

/** null = no month has both stored Brent and stored BoZ FX (a normal empty state, not an error). */
export async function getOilZmLatest(): Promise<OilZmMonth | null> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/oil/zm/latest`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 404) return null;
  if (response.status === 503) throw new ApiError("api", "Stored oil and FX data are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export const getOilZmHistory = (range: OilRange) => getOilJson<OilZmHistory>(`/api/v1/oil/zm/history?range=${range}`);

export const CURVE_TENORS = ["91D", "182D", "273D", "364D", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y"] as const;
export type CurveTenor = (typeof CURVE_TENORS)[number];

type CurvePointBase = {
  tenor: CurveTenor; // the published BoZ tenor bucket exactly as stored; NOT a remaining maturity
  tenor_order: number; // position in the fixed display sequence (categorical)
  yield_rate: string; // percent per annum as published
  yield_unit: "percent_per_annum_as_published";
  auction_date: string;
  tender_number: string;
  isin: string;
  source: "boz";
  source_url: string;
  age_days: number; // as_of minus auction_date
};
export type TreasuryBillCurvePoint = CurvePointBase & { instrument_type: "treasury_bill"; discount_rate: string };
export type GovernmentBondCurvePoint = CurvePointBase & { instrument_type: "government_bond"; coupon_rate: string };
export type CurvePoint = TreasuryBillCurvePoint | GovernmentBondCurvePoint;

/** DERIVED snapshot: each tenor's latest stored accepted BoZ auction observation on or before as_of. The backend owns
 * selection, age and vintage metadata; the UI only renders. Not an official BoZ curve, not fitted or interpolated. */
export type YieldCurve = {
  classification: "derived";
  derived: true;
  curve_type: "published_auction_yield_tenor_curve";
  methodology: string;
  notice: string;
  as_of: string;
  yield_unit: "percent_per_annum_as_published";
  point_count: number;
  missing_tenors: CurveTenor[];
  oldest_point_date: string;
  newest_point_date: string;
  mixed_vintage: boolean;
  vintage_span_days: number;
  points: CurvePoint[];
};

/** asOf null = latest (Africa/Lusaka today). null result = no stored accepted observation on or before the date. */
export async function getYieldCurve(asOf: string | null): Promise<YieldCurve | null> {
  const path = asOf ? `/api/v1/yield-curve/as-of?date=${encodeURIComponent(asOf)}` : "/api/v1/yield-curve/latest";
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 404) return null;
  if (response.status === 422) throw new ApiError("api", "That as-of date is not accepted (it must be a past or present date)");
  if (response.status === 503) throw new ApiError("api", "Stored auction data is unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export type SpreadId = "10Y_364D" | "15Y_364D" | "10Y_2Y" | "15Y_91D";
/** long_yield_rate minus short_yield_rate in basis points, computed by the backend from the derived curve snapshot. The
 * two endpoints may come from different auction dates (`same_vintage`, `vintage_gap_days`). No interpretation attached. */
export type YieldSpreadPoint = {
  classification: "derived";
  derived: true;
  metric_type: "published_tenor_yield_spread";
  spread_id: SpreadId;
  short_tenor: CurveTenor;
  long_tenor: CurveTenor;
  short_yield_rate: string; // percent per annum as published
  long_yield_rate: string;
  spread_bps: string; // exact decimal string; may be negative or zero
  short_auction_date: string;
  long_auction_date: string;
  same_vintage: boolean;
  vintage_gap_days: number;
  yield_unit: "percent_per_annum_as_published";
  spread_unit: "basis_points";
};
export type MissingYieldSpread = { spread_id: SpreadId; short_tenor: CurveTenor; long_tenor: CurveTenor; missing_tenors: CurveTenor[] };
export type YieldSpreads = {
  classification: "derived";
  derived: true;
  metric_type: "published_tenor_yield_spread";
  curve_type: "published_auction_yield_tenor_curve";
  methodology: string;
  notice: string;
  as_of: string;
  available_spreads: YieldSpreadPoint[];
  missing_spreads: MissingYieldSpread[];
};

/** Spreads for exactly one as-of date (the curve's own as_of), so spreads and curve always share one snapshot. */
export async function getYieldSpreads(asOf: string): Promise<YieldSpreads | null> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/yield-curve/spreads/as-of?date=${encodeURIComponent(asOf)}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 404) return null;
  if (response.status === 422) throw new ApiError("api", "That as-of date is not accepted (it must be a past or present date)");
  if (response.status === 503) throw new ApiError("api", "Stored auction data is unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

/** Bitcoin. BTC/USD is Bitstamp's BTC/USD SPOT last trade (one exchange's market, not a global reference price). Decimals are
 * plain-notation strings; the UI only presents and rounds, and the API owns every calculation, age and stale state. */
export const BTC_RANGES = ["1M", "3M", "6M", "1Y", "3Y", "5Y", "ALL"] as const;
export type BtcRange = (typeof BTC_RANGES)[number];

export type BtcUsdQuote = {
  symbol: "BTCUSD";
  market: "BTC/USD";
  market_type: "SPOT";
  price_type: "last_trade";
  last: string;
  bid: string;
  ask: string;
  high_24h: string; // rolling last 24 hours, not the UTC day
  low_24h: string;
  vwap_24h: string;
  volume_24h_btc: string;
  observed_at: string; // Bitstamp ticker timestamp
  retrieved_at: string;
  source: "bitstamp";
  source_url: string;
  stale: boolean;
  stale_after_seconds: number;
};

/** One completed UTC-day candle: close is the last trade of that day, not an average. */
export type BtcDailyCandle = { candle_open_at: string; open: string; high: string; low: string; close: string; volume_btc: string };
export type BtcUsdHistory = { market: "BTC/USD"; provider: "bitstamp"; interval: "1d"; range: BtcRange; count: number; observations: BtcDailyCandle[] };

/** DERIVED live conversion; never an exchange-traded BTC/ZMW, dealer, P2P or mobile-money quote. */
export type BtcZmwQuote = {
  classification: "derived";
  derived: true;
  symbol: "BTCZMW";
  methodology_id: "btc_zmw_live_v1";
  methodology: string;
  notice: string;
  btc_zmw: string;
  currency: "ZMW";
  unit: "bitcoin";
  btc_usd_last: string;
  btc_source: "bitstamp";
  btc_market: "BTC/USD";
  btc_price_type: "last_trade";
  btc_observed_at: string;
  btc_source_url: string;
  btc_age_seconds: number;
  btc_stale: boolean;
  usd_zmw_mid: string;
  fx_source: "boz";
  fx_pair: "USDZMW";
  fx_price: "mid";
  fx_observed_at: string;
  fx_source_url: string;
  fx_age_seconds: number;
  fx_stale: boolean;
  derived_at: string;
  derived_stale: boolean;
};

async function getBtcJson<T>(path: string, messages: Partial<Record<number, string>>): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  const message = messages[response.status];
  if (message) throw new ApiError(response.status === 502 || response.status === 504 ? "upstream" : "api", message);
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

const BITSTAMP_DOWN = { 502: "Bitstamp data is currently unavailable or invalid", 504: "Bitstamp request timed out" };
export const getBtcUsdLatest = () => getBtcJson<BtcUsdQuote>("/api/v1/btc/usd/latest", BITSTAMP_DOWN);
export const getBtcUsdHistory = async (range: BtcRange) =>
  (await getBtcJson<BtcUsdHistory>(`/api/v1/btc/usd/history?range=${range}`, { 503: "Stored BTC/USD history is unavailable (database)" })).observations;
export const getBtcZmwLatest = () => getBtcJson<BtcZmwQuote>("/api/v1/btc/zmw/latest", {
  ...BITSTAMP_DOWN,
  404: "No stored Bank of Zambia USD/ZMW observation to convert with",
  503: "Stored USD/ZMW observations are unavailable (database)",
});
