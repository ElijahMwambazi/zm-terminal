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
