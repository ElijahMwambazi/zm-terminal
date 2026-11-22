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

/** Official BoZ monthly averages (Fortnightly Statistics Table 02). Discrete historical observations: no timestamp, no stale flag.
 * Rates are decimal strings exactly as published; `mid` is the published mid, never derived. */
export const MONTHLY_RANGES = ["1Y", "3Y", "5Y", "ALL"] as const;
export type MonthlyRange = (typeof MONTHLY_RANGES)[number];

export type FxMonthlyPoint = {
  month: string; // YYYY-MM
  buy: string;
  mid: string;
  sell: string;
  source: "boz";
  source_series: string;
  source_publication: string;
  source_url: string;
  source_page: number;
  published_precision: number;
};

export type FxMonthlyHistory = {
  symbol: "USDZMW";
  range: MonthlyRange;
  count: number;
  first_month: string | null;
  last_month: string | null;
  observations: FxMonthlyPoint[];
};

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const DECIMAL_RE = /^\d+(\.\d+)?$/; // plain finite decimal string; NaN, Infinity, signs and exponents do not match
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const isPositiveDecimal = (value: unknown): value is string => typeof value === "string" && DECIMAL_RE.test(value) && /[1-9]/.test(value);
const isCount = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0;

function validMonthlyPoint(value: unknown): value is FxMonthlyPoint {
  return isRecord(value) && typeof value.month === "string" && MONTH_RE.test(value.month)
    && isPositiveDecimal(value.buy) && isPositiveDecimal(value.mid) && isPositiveDecimal(value.sell)
    && value.source === "boz" && isText(value.source_series) && isText(value.source_publication) && isText(value.source_url)
    && typeof value.source_page === "number" && Number.isInteger(value.source_page) && value.source_page > 0
    && typeof value.published_precision === "number" && Number.isInteger(value.published_precision) && value.published_precision >= 0;
}

/** Fails closed: any response that does not match the monthly contract is an ApiError, never a partially trusted value.
 * Values are checked, not converted: decimals stay the exact strings the API sent. Months must be strictly ascending (unique). */
export function parseMonthlyHistory(body: unknown, requested: MonthlyRange): FxMonthlyHistory {
  const fail = (): never => { throw new ApiError("api", "Unexpected monthly response"); };
  if (!isRecord(body) || body.symbol !== "USDZMW" || body.range !== requested || !isCount(body.count)
    || !Array.isArray(body.observations) || body.count !== body.observations.length) return fail();
  const observations = body.observations;
  if (!observations.every(validMonthlyPoint)) return fail();
  const points = observations as FxMonthlyPoint[];
  for (let i = 1; i < points.length; i++) if (points[i].month <= points[i - 1].month) return fail();
  if (points.length === 0) {
    if (body.first_month !== null || body.last_month !== null) return fail(); // empty result: both bounds are null
  } else if (body.first_month !== points[0].month || body.last_month !== points[points.length - 1].month) {
    return fail();
  }
  return body as unknown as FxMonthlyHistory;
}

export async function getUsdZmwMonthly(range: MonthlyRange): Promise<FxMonthlyHistory> {
  return parseMonthlyHistory(await getJson<unknown>(`/api/v1/fx/USDZMW/monthly?range=${range}`), range);
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

/** One derived day (DERIVED historical BTC/ZMW): the completed Bitstamp UTC-day close x the mean of that Zambia date's stored
 * BoZ mids. `date` is a calendar association, not a timestamp; the inputs are not simultaneous. The API does all arithmetic. */
export type BtcZmwDailyPoint = {
  classification: "derived";
  derived: true;
  date: string;
  btc_zmw: string;
  currency: "ZMW";
  unit: "bitcoin";
  btc_usd_close: string;
  btc_source: "bitstamp";
  btc_market: "BTC/USD";
  btc_price_type: "daily_close";
  btc_candle_open_at: string;
  usd_zmw_daily_mid_avg: string;
  fx_source: "boz";
  fx_pair: "USDZMW";
  fx_price: "daily_mid_average";
  fx_observation_count: number;
  fx_first_observation: string;
  fx_last_observation: string;
};
export type BtcZmwHistory = {
  classification: "derived";
  derived: true;
  range: BtcRange;
  count: number;
  btc_days_without_fx: number;
  fx_stored_from: string | null; // first Africa/Lusaka date with stored BoZ USD/ZMW: the availability boundary
  methodology_id: "btc_zmw_daily_v1";
  methodology: string;
  notice: string;
  observations: BtcZmwDailyPoint[];
};
export const getBtcZmwHistory = (range: BtcRange) =>
  getBtcJson<BtcZmwHistory>(`/api/v1/btc/zmw/history?range=${range}`, { 503: "Stored BTC/USD or USD/ZMW data is unavailable (database)" });

/** Global Copper benchmark: IMF Primary Commodity Prices, series PCOPP, a MONTHLY average in nominal USD per metric ton.
 * Read from storage only (the UI never calls the IMF). The IMF is the provider; the definition names LME as the underlying
 * benchmark source. Not a Zambia copper price, not a live quote, not a futures price. Decimals are plain strings. */
export const COPPER_RANGES = ["1Y", "3Y", "5Y", "ALL"] as const;
export type CopperRange = (typeof COPPER_RANGES)[number];
export type CopperPoint = { benchmark: "COPPER"; period_start: string; period_end: string; value: string; currency: "USD"; unit: "metric_ton"; source: "imf"; price_basis: "monthly_average" };
export type CopperObservation = CopperPoint & {
  source_series: "PCOPP";
  source_definition: string;
  source_url: string; // the exact IMF request that supplied the stored value
  source_page_url: string;
  retrieved_at: string;
  stale: boolean;
  /** Backend heuristic (calendar months behind the current Africa/Lusaka month); displayed, never hardcoded in the UI. */
  stale_after_months: number;
};
export type CopperLatest = { observation: CopperObservation | null };

async function getCopperJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored copper benchmark is unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export const getCopperLatest = () => getCopperJson<CopperLatest>("/api/v1/copper/latest");
export async function getCopperHistory(range: CopperRange): Promise<CopperPoint[]> {
  return (await getCopperJson<{ observations: CopperPoint[] }>(`/api/v1/copper/history?range=${range}`)).observations;
}

/** COPPER ZM: a DERIVED monthly ZM Terminal indicator (IMF Copper USD/mt x BoZ USD/ZMW observed-days mid average), never an
 * official series and not a Zambia copper price. The API does all arithmetic; the UI only displays and rounds for presentation. */
export type CopperZmPoint = {
  classification: "derived";
  derived: true;
  period_start: string;
  period_end: string;
  currency: "ZMW";
  unit: "metric_ton";
  zmw_per_mt: string;
  copper_usd_per_mt: string;
  usd_zmw_observed_days_avg: string;
  fx_day_count: number;
  fx_observation_count: number;
  /** partial_source_window: stored BoZ history begins during this month. observed_days: uses whatever stored days exist; asserts no completeness. */
  fx_coverage: "partial_source_window" | "observed_days";
};
export type CopperZmMonth = CopperZmPoint & {
  methodology_id: string;
  methodology: string;
  notice: string;
  timing_note: string;
  fx_first_date: string;
  fx_last_date: string;
  fx_first_observation: string;
  fx_last_observation: string;
  copper_source: "imf";
  copper_source_series: "PCOPP";
  copper_source_definition: string;
  copper_source_url: string;
  copper_source_page_url: string;
  copper_retrieved_at: string;
  fx_source: "boz";
  fx_pair: "USDZMW";
  fx_price: "mid";
  fx_source_url: string;
  stale: boolean;
  stale_after_months: number;
};
export type CopperZmHistory = {
  range: CopperRange;
  count: number;
  copper_months_without_fx: number;
  methodology_id: string;
  methodology: string;
  notice: string;
  timing_note: string;
  observations: CopperZmPoint[];
};

/** null = no month has both stored IMF Copper and stored BoZ FX (a normal empty state, not an error). */
export async function getCopperZmLatest(): Promise<CopperZmMonth | null> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/copper/zm/latest`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 404) return null;
  if (response.status === 503) throw new ApiError("api", "Stored Copper and FX data are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

export async function getCopperZmHistory(range: CopperRange): Promise<CopperZmHistory> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/copper/zm/history?range=${range}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored Copper and FX data are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

/** Copper-Kwacha correlation: a DESCRIPTIVE Pearson coefficient between monthly changes in IMF Copper USD and the stored BoZ
 * USD/ZMW observed-days average, calculated by the API from stored data. Not causal, not predictive; no significance testing. */
export type CorrelationWindow = "6M" | "12M";
export const CORRELATION_WINDOWS: readonly CorrelationWindow[] = ["6M", "12M"];
export type CopperKwachaCorrelation = {
  range: CopperRange;
  window: CorrelationWindow;
  window_changes: number;
  methodology_id: string;
  methodology: string;
  basis_note: string;
  notice: string;
  variables: string[];
  matched_months: number;
  candidate_changes: number;
  changes_used: number;
  changes_excluded_gap: number;
  changes_excluded_partial: number;
  changes_excluded_nonpositive: number;
  full_sample: {
    status: "available" | "insufficient_sample" | "zero_variance";
    correlation: string | null;
    sample_size: number;
    first_change_month: string | null;
    last_change_month: string | null;
    small_sample: boolean;
    reason: string | null;
  };
  rolling: { window_end_month: string; correlation: string | null; sample_size: number }[];
  rolling_reason: string | null;
};

export async function getCopperKwachaCorrelation(range: CopperRange, window: CorrelationWindow): Promise<CopperKwachaCorrelation> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/copper/kwacha/correlation?range=${range}&window=${window}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored Copper and FX data are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

/** Copper-Kwacha historical sensitivity (beta): a DESCRIPTIVE full-sample statistic calculated by the API on the same eligible
 * monthly changes as the correlation. Dependent: USD/ZMW change; explanatory: Copper USD change. Not causal, not a forecast. */
export type CopperKwachaSensitivity = {
  range: CopperRange;
  methodology_id: string;
  methodology: string;
  notice: string;
  units: string;
  sign_note: string;
  matched_months: number;
  candidate_changes: number;
  changes_used: number;
  changes_excluded_gap: number;
  changes_excluded_partial: number;
  changes_excluded_nonpositive: number;
  first_change_month: string | null;
  last_change_month: string | null;
  sample_size: number;
  status: "available" | "insufficient_sample" | "zero_copper_variance";
  beta: string | null;
  small_sample: boolean;
  reason: string | null;
};

export async function getCopperKwachaSensitivity(range: CopperRange): Promise<CopperKwachaSensitivity> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/copper/kwacha/sensitivity?range=${range}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored Copper and FX data are unavailable (database)");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  return response.json();
}

/* ---------- Energy: stored ERB annual generation (Phase 1; local/internal, rights review_required) ----------
 * Values are plain-decimal GWh strings, kept exactly as the API sends them. Responses are validated at runtime and rejected whole. */
export const ENERGY_STATION_GROUPS = ["zesco_large_hydro", "zesco_mini_hydro", "zesco_diesel", "independent_power_producer"] as const;
export type EnergyStationGroup = (typeof ENERGY_STATION_GROUPS)[number];
export const ENERGY_GROUP_LABEL: Record<EnergyStationGroup, string> = {
  zesco_large_hydro: "ZESCO LARGE HYDRO",
  zesco_mini_hydro: "ZESCO MINI HYDRO",
  zesco_diesel: "ZESCO DIESEL",
  independent_power_producer: "INDEPENDENT POWER PRODUCERS",
};
export const ENERGY_GROUP_SHORT: Record<EnergyStationGroup, string> = {
  zesco_large_hydro: "LARGE HYDRO",
  zesco_mini_hydro: "MINI HYDRO",
  zesco_diesel: "DIESEL",
  independent_power_producer: "IPPs",
};
export type EnergyValueState = "reported" | "not_reported";
type EnergyCell = {
  value_state: EnergyValueState;
  generation_gwh: string | null;
  published_precision: number | null;
  source_table: string;
  source_pdf_page: number;
  source_printed_page: number;
};
export type EnergyGenerationObservation = EnergyCell & { station_group: EnergyStationGroup; station_name: string; observation_year: number };
export type EnergyGenerationGroupTotal = {
  station_group: EnergyStationGroup; observation_year: number; total_gwh: string; published_precision: number;
  source_table: string; source_pdf_page: number; source_printed_page: number;
};
export type EnergyGenerationSnapshot = {
  publisher: string; source_series: string; publication_year: number; revision_sequence: number; title: string;
  cover_date: string | null; source_url: string; rights_status: string; observation_year: number;
  stations: EnergyGenerationObservation[]; group_totals: EnergyGenerationGroupTotal[];
};
export type EnergyGenerationLatest = { snapshot: EnergyGenerationSnapshot | null };
export type EnergyGenerationHistoryPoint = EnergyCell & { observation_year: number; publication_year: number; revision_sequence: number };
export type EnergyGenerationHistory = {
  station_group: EnergyStationGroup; station_name: string; count: number; rights_status: string; observations: EnergyGenerationHistoryPoint[];
};
export type EnergyGenerationGroupHistoryPoint = {
  station_group: EnergyStationGroup; observation_year: number; total_gwh: string; published_precision: number;
  publication_year: number; revision_sequence: number; source_table: string; source_pdf_page: number; source_printed_page: number;
};
export type EnergyGenerationGroupHistory = {
  station_group: EnergyStationGroup; count: number; rights_status: string | null; observations: EnergyGenerationGroupHistoryPoint[];
};

class EnergyShapeError extends Error {}
const bad = (what: string): never => { throw new EnergyShapeError(what); };
const rec = (v: unknown, what: string): Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : bad(what);
const str = (v: unknown, what: string): string => (typeof v === "string" ? v : bad(what));
const int = (v: unknown, what: string, min = 0): number => (typeof v === "number" && Number.isInteger(v) && v >= min ? v : bad(what));
const arr = (v: unknown, what: string): unknown[] => (Array.isArray(v) ? v : bad(what));
const PLAIN_DECIMAL = /^[0-9]+(\.[0-9]+)?$/;
const decimal = (v: unknown, what: string): string => (typeof v === "string" && PLAIN_DECIMAL.test(v) ? v : bad(what));
const group = (v: unknown): EnergyStationGroup =>
  (ENERGY_STATION_GROUPS as readonly unknown[]).includes(v) ? (v as EnergyStationGroup) : bad("station_group");

function cell(o: Record<string, unknown>): EnergyCell {
  const state = o.value_state;
  if (state !== "reported" && state !== "not_reported") bad("value_state");
  const gwh = o.generation_gwh ?? null;
  const precision = o.published_precision ?? null;
  if (state === "reported") {
    decimal(gwh, "generation_gwh"); int(precision, "published_precision");
  } else if (gwh !== null || precision !== null) bad("not_reported cell carries a value");
  return {
    value_state: state as EnergyValueState, generation_gwh: gwh as string | null, published_precision: precision as number | null,
    source_table: str(o.source_table, "source_table"), source_pdf_page: int(o.source_pdf_page, "source_pdf_page", 1),
    source_printed_page: int(o.source_printed_page, "source_printed_page", 1),
  };
}

export function parseEnergyLatest(raw: unknown): EnergyGenerationLatest {
  const top = rec(raw, "latest");
  if (top.snapshot === null) return { snapshot: null };
  const s = rec(top.snapshot, "snapshot");
  const year = int(s.observation_year, "observation_year");
  const seen = new Set<string>();
  const stations = arr(s.stations, "stations").map((item) => {
    const o = rec(item, "station");
    const out: EnergyGenerationObservation = {
      ...cell(o), station_group: group(o.station_group), station_name: str(o.station_name, "station_name"),
      observation_year: int(o.observation_year, "observation_year"),
    };
    if (out.observation_year !== year) bad("station year mismatch");
    const key = JSON.stringify([out.station_group, out.station_name]);
    if (seen.has(key)) bad("duplicate station");
    seen.add(key);
    return out;
  });
  const totalGroups = new Set<string>();
  const group_totals = arr(s.group_totals, "group_totals").map((item) => {
    const o = rec(item, "group total");
    const out: EnergyGenerationGroupTotal = {
      station_group: group(o.station_group), observation_year: int(o.observation_year, "observation_year"),
      total_gwh: decimal(o.total_gwh, "total_gwh"), published_precision: int(o.published_precision, "published_precision"),
      source_table: str(o.source_table, "source_table"), source_pdf_page: int(o.source_pdf_page, "source_pdf_page", 1),
      source_printed_page: int(o.source_printed_page, "source_printed_page", 1),
    };
    if (out.observation_year !== year) bad("total year mismatch");
    if (totalGroups.has(out.station_group)) bad("duplicate group total");
    totalGroups.add(out.station_group);
    return out;
  });
  return {
    snapshot: {
      publisher: str(s.publisher, "publisher"), source_series: str(s.source_series, "source_series"),
      publication_year: int(s.publication_year, "publication_year"), revision_sequence: int(s.revision_sequence, "revision_sequence", 1),
      title: str(s.title, "title"), cover_date: s.cover_date === null ? null : str(s.cover_date, "cover_date"),
      source_url: str(s.source_url, "source_url"), rights_status: str(s.rights_status, "rights_status"),
      observation_year: year, stations, group_totals,
    },
  };
}

function ascending(years: number[]) {
  for (let i = 1; i < years.length; i++) if (years[i] <= years[i - 1]) bad("years not strictly ascending");
}

export function parseEnergyHistory(raw: unknown): EnergyGenerationHistory {
  const o = rec(raw, "history");
  const observations = arr(o.observations, "observations").map((item): EnergyGenerationHistoryPoint => {
    const p = rec(item, "point");
    return {
      ...cell(p), observation_year: int(p.observation_year, "observation_year"),
      publication_year: int(p.publication_year, "publication_year"), revision_sequence: int(p.revision_sequence, "revision_sequence", 1),
    };
  });
  if (int(o.count, "count") !== observations.length) bad("count mismatch");
  ascending(observations.map((p) => p.observation_year));
  return {
    station_group: group(o.station_group), station_name: str(o.station_name, "station_name"), count: observations.length,
    rights_status: str(o.rights_status, "rights_status"), observations,
  };
}

export function parseEnergyGroupHistory(raw: unknown, expected?: EnergyStationGroup): EnergyGenerationGroupHistory {
  const o = rec(raw, "group history");
  const g = group(o.station_group);
  if (expected && g !== expected) bad("group mismatch");
  const observations = arr(o.observations, "observations").map((item): EnergyGenerationGroupHistoryPoint => {
    const p = rec(item, "point");
    const out = {
      station_group: group(p.station_group), observation_year: int(p.observation_year, "observation_year"),
      total_gwh: decimal(p.total_gwh, "total_gwh"), published_precision: int(p.published_precision, "published_precision"),
      publication_year: int(p.publication_year, "publication_year"), revision_sequence: int(p.revision_sequence, "revision_sequence", 1),
      source_table: str(p.source_table, "source_table"), source_pdf_page: int(p.source_pdf_page, "source_pdf_page", 1),
      source_printed_page: int(p.source_printed_page, "source_printed_page", 1),
    };
    if (out.station_group !== g) bad("point group mismatch");
    return out;
  });
  if (int(o.count, "count") !== observations.length) bad("count mismatch");
  ascending(observations.map((p) => p.observation_year));
  return {
    station_group: g, count: observations.length,
    rights_status: o.rights_status === null ? null : str(o.rights_status, "rights_status"), observations,
  };
}

/** Station-history 404 is a distinct state; 503 is the stored-data outage. Raw server text is never shown. */
export class EnergyNotFoundError extends ApiError {
  constructor() { super("api", "No stored ERB generation history for that station"); }
}

async function getEnergyJson<T>(path: string, parse: (raw: unknown) => T): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored ERB generation data is unavailable (database)");
  if (response.status === 404) throw new EnergyNotFoundError();
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  try {
    return parse(await response.json());
  } catch {
    throw new ApiError("api", "ZM Terminal API returned an invalid Energy response");
  }
}

export const getEnergyGenerationLatest = () => getEnergyJson("/api/v1/energy/generation/latest", parseEnergyLatest);
export const getEnergyStationHistory = (stationGroup: EnergyStationGroup, stationName: string) =>
  getEnergyJson(`/api/v1/energy/generation/history?${new URLSearchParams({ station_group: stationGroup, station_name: stationName })}`, parseEnergyHistory);
export const getEnergyGroupHistory = (stationGroup: EnergyStationGroup) =>
  getEnergyJson(`/api/v1/energy/generation/group-history?${new URLSearchParams({ station_group: stationGroup })}`,
    (raw) => parseEnergyGroupHistory(raw, stationGroup));

/* ---------- Bank FX: stored BoZ commercial-bank USD/ZMW rates (local/internal, rights review_required) ----------
 * Stored-only: these functions read the ZM Terminal API, which reads PostgreSQL. Nothing here contacts BoZ. Rate values are the exact
 * published strings and are NEVER converted through Number: they are validated by pattern only and kept as received. */
export const BANK_FX_SLOTS = ["09:30", "12:30", "15:30"] as const;
export type BankFxSlot = (typeof BANK_FX_SLOTS)[number];
export type BankFxLayout = "v1" | "v2";
export type BankFxInstitutionRate = {
  source_row_index: number; institution_name: string; institution_name_raw: string;
  interbank_buy: string; interbank_sell: string; retail_buy: string; retail_sell: string;
};
export type BankFxSlotSnapshot = {
  slot: BankFxSlot; retail_source_label: "Retail Rates" | "BOARD RATES"; banks: BankFxInstitutionRate[]; market_average: BankFxInstitutionRate | null;
};
export type BankFxSnapshot = {
  observation_date: string; source_record_id: string; source_revision_id: number; source_internal_nid: number;
  source_created: string; source_changed: string; source_revision_timestamp: string; source_title: string;
  source_rates_datetime_raw: string; source_rates_datetime: string; table_layout: BankFxLayout; retrieved_at: string; source_url: string;
  rights_status: string; slots: BankFxSlotSnapshot[];
};
export type BankFxLatest = { snapshot: BankFxSnapshot | null };

const BANK_FX_RATE = /^[0-9]+\.[0-9]{1,4}$/;
const BANK_FX_RETAIL_LABEL: Record<BankFxLayout, BankFxSlotSnapshot["retail_source_label"]> = { v1: "Retail Rates", v2: "BOARD RATES" };
const nonEmpty = (v: unknown, what: string): string => (typeof v === "string" && v.length > 0 ? v : bad(what));
const rate = (v: unknown, what: string): string => (typeof v === "string" && BANK_FX_RATE.test(v) && /[1-9]/.test(v) ? v : bad(what));
/** Explicit Gregorian validation (no `Date`, so years 0000-0099 are not remapped to 1900-1999). */
function validGregorian(year: number, month: number, day: number): boolean {
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return day <= days;
}
function calendarDate(v: unknown, what: string): string {
  const text = nonEmpty(v, what);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) return bad(what);
  return validGregorian(Number(m[1]), Number(m[2]), Number(m[3])) ? text : bad(what);
}
// Backend (pydantic) datetimes: ISO date + "T" + time, optional 1-6 digit fraction, mandatory "Z" or numeric UTC offset.
const BANK_FX_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/;
function instant(v: unknown, what: string): string {
  const text = nonEmpty(v, what);
  const m = BANK_FX_INSTANT.exec(text);
  if (!m) return bad(what);
  const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number);
  const offsetOk = m[7] === undefined || (Number(m[7]) <= 23 && Number(m[8]) <= 59);
  return validGregorian(year, month, day) && hour <= 23 && minute <= 59 && second <= 59 && offsetOk ? text : bad(what);
}
const isMarketAverageName = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase() === "market average";

function institution(raw: unknown, what: string): BankFxInstitutionRate {
  const o = rec(raw, what);
  return {
    source_row_index: int(o.source_row_index, "source_row_index", 3), institution_name: nonEmpty(o.institution_name, "institution_name"),
    institution_name_raw: nonEmpty(o.institution_name_raw, "institution_name_raw"),
    interbank_buy: rate(o.interbank_buy, "interbank_buy"), interbank_sell: rate(o.interbank_sell, "interbank_sell"),
    retail_buy: rate(o.retail_buy, "retail_buy"), retail_sell: rate(o.retail_sell, "retail_sell"),
  };
}

function slotSnapshot(raw: unknown, layout: BankFxLayout): BankFxSlotSnapshot {
  const o = rec(raw, "slot");
  const slot = (BANK_FX_SLOTS as readonly unknown[]).includes(o.slot) ? (o.slot as BankFxSlot) : bad("slot");
  if (o.retail_source_label !== BANK_FX_RETAIL_LABEL[layout]) bad("retail_source_label does not match the layout");
  const banks = arr(o.banks, "banks").map((b) => institution(b, "bank"));
  const average = o.market_average === null || o.market_average === undefined ? null : institution(o.market_average, "market_average");
  if (average && !isMarketAverageName(average.institution_name)) bad("market_average is not the Market Average row");
  const names = new Set<string>();
  const rows = new Set<number>();
  let previous = 0;
  for (const bank of banks) {
    if (bank.source_row_index <= previous) bad("bank rows are not in strictly increasing source order");
    previous = bank.source_row_index;
    if (names.has(bank.institution_name)) bad("duplicate institution in a slot");
    if (isMarketAverageName(bank.institution_name)) bad("market average inside banks");
    names.add(bank.institution_name); rows.add(bank.source_row_index);
  }
  if (average && (rows.has(average.source_row_index) || names.has(average.institution_name))) bad("market average duplicates a bank row");
  return { slot, retail_source_label: BANK_FX_RETAIL_LABEL[layout], banks, market_average: average };
}

export function parseBankFxSnapshot(raw: unknown): BankFxSnapshot {
  const o = rec(raw, "snapshot");
  const layout = o.table_layout === "v1" || o.table_layout === "v2" ? o.table_layout : bad("table_layout");
  const slots = arr(o.slots, "slots").map((s) => slotSnapshot(s, layout));
  if (slots.length === 0) bad("snapshot has no published slot");
  slots.forEach((s, i) => { if (i > 0 && BANK_FX_SLOTS.indexOf(s.slot) <= BANK_FX_SLOTS.indexOf(slots[i - 1].slot)) bad("slots are not unique and chronological"); });
  const source_url = nonEmpty(o.source_url, "source_url");
  let url: URL;
  try { url = new URL(source_url); } catch { return bad("source_url"); }
  if (url.protocol !== "https:") bad("source_url");
  return {
    observation_date: calendarDate(o.observation_date, "observation_date"), source_record_id: nonEmpty(o.source_record_id, "source_record_id"),
    source_revision_id: int(o.source_revision_id, "source_revision_id", 1), source_internal_nid: int(o.source_internal_nid, "source_internal_nid", 1),
    source_created: instant(o.source_created, "source_created"), source_changed: instant(o.source_changed, "source_changed"),
    source_revision_timestamp: instant(o.source_revision_timestamp, "source_revision_timestamp"), source_title: nonEmpty(o.source_title, "source_title"),
    source_rates_datetime_raw: nonEmpty(o.source_rates_datetime_raw, "source_rates_datetime_raw"),
    source_rates_datetime: instant(o.source_rates_datetime, "source_rates_datetime"), table_layout: layout,
    retrieved_at: instant(o.retrieved_at, "retrieved_at"), source_url, rights_status: nonEmpty(o.rights_status, "rights_status"), slots,
  };
}

export function parseBankFxLatest(raw: unknown): BankFxLatest {
  const o = rec(raw, "latest");
  if (!("snapshot" in o)) bad("snapshot");
  return { snapshot: o.snapshot === null ? null : parseBankFxSnapshot(o.snapshot) };
}

const BANK_FX_NOT_FOUND = Symbol("bank-fx-not-found");

async function getBankFxJson(path: string, allowNotFound: boolean): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError("api", "ZM Terminal API is unreachable");
  }
  if (response.status === 503) throw new ApiError("api", "Stored commercial bank FX data is unavailable (database)");
  if (response.status === 404 && allowNotFound) return BANK_FX_NOT_FOUND;
  if (response.status === 422) throw new ApiError("api", "Invalid date for the stored Bank FX lookup");
  if (!response.ok) throw new ApiError("api", `ZM Terminal API error (${response.status})`);
  try {
    return await response.json();
  } catch {
    throw new ApiError("api", "Unexpected Bank FX response");
  }
}

/** Newest stored observation date's canonical snapshot (or `{ snapshot: null }`). Never contacts BoZ. */
export async function getBankFxLatest(): Promise<BankFxLatest> {
  const body = await getBankFxJson("/api/v1/fx/banks/latest", false);
  try {
    return parseBankFxLatest(body);
  } catch {
    throw new ApiError("api", "Unexpected Bank FX response");
  }
}

/** Exactly this stored date; `null` when it is not stored (never another date). Never contacts BoZ. */
export async function getBankFxDate(day: string): Promise<BankFxSnapshot | null> {
  const body = await getBankFxJson(`/api/v1/fx/banks/history?${new URLSearchParams({ date: day })}`, true);
  if (body === BANK_FX_NOT_FOUND) return null;
  try {
    const snapshot = parseBankFxSnapshot(body);
    if (snapshot.observation_date !== day) bad("observation_date differs from the requested date");
    return snapshot;
  } catch {
    throw new ApiError("api", "Unexpected Bank FX response");
  }
}
