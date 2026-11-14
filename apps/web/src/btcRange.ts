import type { BtcRange } from "./api";

/** Rolling-day windows of the BTC history ranges; they mirror app/services/btc_store.py. ALL is unbounded (null). */
export const BTC_RANGE_DAYS: Record<BtcRange, number | null> = {
  "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "3Y": 1095, "5Y": 1825, ALL: null,
};
const DAY_MS = 86_400_000;

/** The earliest UTC calendar date (YYYY-MM-DD) a range requests, or null for ALL. Like the backend, the window starts at the
 * UTC midnight of (request time - window), so this is the UTC date of that instant. */
export function requestedStartDate(range: BtcRange, requestedAtMs: number): string | null {
  const days = BTC_RANGE_DAYS[range];
  return days === null ? null : new Date(requestedAtMs - days * DAY_MS).toISOString().slice(0, 10);
}

/** Whether the requested BTC window begins strictly before the stored BoZ source boundary (`fx_stored_from`, a global date that
 * is independent of which days were derived). ALL always qualifies when a boundary exists. A start exactly on the boundary does
 * not extend before it. ISO dates compare chronologically as strings. */
export function extendsBeforeFxHistory(range: BtcRange, fxStoredFrom: string | null, requestedAtMs: number): boolean {
  if (fxStoredFrom === null) return false;
  const start = requestedStartDate(range, requestedAtMs);
  return start === null || start < fxStoredFrom;
}
