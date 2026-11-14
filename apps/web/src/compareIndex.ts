/** Visualization-only rebasing for the Copper-Kwacha comparison: index_t = (x_t / x_0) * 100.
 * Inputs are the already-calculated decimal strings from the API; the result is for drawing only and is never stored.
 * Returns null (no index) when the baseline is zero or any value is not finite, so nothing is divided by zero or invented. */
export function rebaseTo100(values: string[]): number[] | null {
  if (values.length === 0) return null;
  const numbers = values.map((value) => Number(value));
  if (numbers.some((n) => !Number.isFinite(n))) return null;
  const base = numbers[0];
  if (base === 0) return null;
  const indexed = numbers.map((n) => (n / base) * 100);
  return indexed.every((n) => Number.isFinite(n)) ? indexed : null;
}
