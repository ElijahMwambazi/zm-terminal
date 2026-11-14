/** Y-axis scale for the categorical curve chart: evenly spaced ticks from `low` through `top`, where `top` is a whole
 * number of steps above `low` and never below the largest value plus headroom. Drawing geometry only. */
export function curveScale(values: number[]) {
  const low = Math.floor(Math.min(...values) - 0.5);
  const high = Math.ceil(Math.max(...values) + 0.5);
  const step = high - low <= 6 ? 1 : 2;
  const top = low + Math.ceil((high - low) / step) * step; // complete interval: the last tick is never below `high`
  const ticks: number[] = [];
  for (let t = low; t <= top; t += step) ticks.push(t);
  return { low, top, step, ticks };
}
