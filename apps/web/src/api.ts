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
