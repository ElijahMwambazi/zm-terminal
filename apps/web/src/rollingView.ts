/** Presentation classification of rolling-correlation windows. The existence of a window comes from the API's entries
 * themselves; a null coefficient (zero variance in that window) is a defined window with an undefined Pearson r, never
 * "no window", never zero, and never plotted. */
export type RollingEntry = { window_end_month: string; correlation: string | null; sample_size: number };
export type RollingKind = "none" | "all_numeric" | "mixed" | "all_undefined";

export function classifyRolling(rolling: RollingEntry[]) {
  const numeric = rolling.filter((p) => p.correlation !== null);
  const undefinedCount = rolling.length - numeric.length;
  const kind: RollingKind = rolling.length === 0 ? "none" : undefinedCount === 0 ? "all_numeric" : numeric.length === 0 ? "all_undefined" : "mixed";
  return { rows: rolling, numeric, undefinedCount, kind };
}
