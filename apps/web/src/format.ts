const lusaka = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Lusaka",
  day: "2-digit", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

/** Africa/Lusaka is UTC+2 year-round (CAT). */
export const formatLusaka = (iso: string) => `${lusaka.format(new Date(iso))} CAT`;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A calendar date (YYYY-MM-DD) has no time zone: format it without converting. */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-");
  return `${day} ${MONTHS[Number(month) - 1]} ${year}`;
}

/** A pricing month (first day of the month, YYYY-MM-DD), e.g. "Oct 2026". */
export function formatMonth(isoDate: string): string {
  const [year, month] = isoDate.split("-");
  return `${MONTHS[Number(month) - 1]} ${year}`;
}

/**
 * Presentation-only rounding of a decimal string (half away from zero) with no floating point, so long exact
 * decimals from the API round correctly. group=true adds thousands separators. Non-numeric input returns "—".
 */
export function formatFixed(value: string, places: number, group = false): string {
  const match = /^(-?)([0-9]+)(?:\.([0-9]+))?$/.exec(value.trim());
  if (!match) return "—";
  const [, sign, whole, fraction = ""] = match;
  let scaled = BigInt(whole + fraction.padEnd(places, "0").slice(0, places));
  if (fraction.length > places && Number(fraction[places]) >= 5) scaled += 1n;
  const digits = scaled.toString().padStart(places + 1, "0");
  let integer = digits.slice(0, digits.length - places);
  const decimals = places > 0 ? digits.slice(digits.length - places) : "";
  if (group) integer = integer.replace(/\B(?=([0-9]{3})+(?![0-9]))/g, ",");
  return `${sign === "-" && scaled !== 0n ? "-" : ""}${integer}${places > 0 ? `.${decimals}` : ""}`;
}
