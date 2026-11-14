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
  source_url: string;
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
