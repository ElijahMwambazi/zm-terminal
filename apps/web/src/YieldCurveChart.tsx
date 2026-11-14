import { CURVE_TENORS, type CurvePoint, type CurveTenor } from "./api";
import { formatDate, formatFixed } from "./format";

const W = 460, H = 270, LEFT = 40, RIGHT = 10, TOP = 30, BOTTOM = 32; // narrow viewBox keeps text legible when scaled to a phone
const BILL = "#ffb000", BOND = "#7cc4ff";
const slotX = (index: number) => LEFT + ((index + 0.5) * (W - LEFT - RIGHT)) / CURVE_TENORS.length;

/**
 * Categorical tenor chart: one fixed slot per published tenor bucket (NOT a maturity axis). Only the markers are stored
 * observations. The thin dashed connector is visual only, is never drawn across a missing tenor, and nothing between
 * markers is a value: hovering reads only a marker's own stored observation (native tooltip on the marker).
 */
export default function YieldCurveChart({ points, missing }: { points: CurvePoint[]; missing: CurveTenor[] }) {
  const numbers = points.map((p) => Number(p.yield_rate)); // drawing coordinates only; displayed values stay API strings
  const low = Math.floor(Math.min(...numbers) - 0.5), high = Math.ceil(Math.max(...numbers) + 0.5);
  const step = high - low <= 6 ? 1 : 2;
  const ticks: number[] = [];
  for (let t = low; t <= high; t += step) ticks.push(t);
  const top = ticks[ticks.length - 1];
  const y = (value: number) => TOP + ((top - value) * (H - TOP - BOTTOM)) / (top - low);
  const bySlot = new Map(points.map((p) => [CURVE_TENORS.indexOf(p.tenor), p]));
  const missingSet = new Set(missing);
  const segments: [CurvePoint, CurvePoint][] = [];
  for (let i = 0; i < CURVE_TENORS.length - 1; i += 1) {
    const a = bySlot.get(i), b = bySlot.get(i + 1);
    if (a && b) segments.push([a, b]); // adjacent present slots only: no line across a gap
  }
  const divider = (slotX(3) + slotX(4)) / 2;
  return (
    <svg className="curve-svg" viewBox={`0 0 ${W} ${H}`} role="img" preserveAspectRatio="xMidYMid meet"
      aria-label="Zambia sovereign published auction yield by BoZ tenor bucket">
      <desc>Markers are stored accepted BoZ auction yields at their published tenors. The dashed line is a visual connector only; there are no interpolated yields.</desc>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={LEFT} x2={W - RIGHT} y1={y(t)} y2={y(t)} stroke="#1c1c1c" />
          <text x={LEFT - 8} y={y(t) + 3} textAnchor="end" fill="#8c8c8c" fontSize="11">{t}%</text>
        </g>
      ))}
      <line x1={divider} x2={divider} y1={TOP - 6} y2={H - BOTTOM} stroke="#333" strokeDasharray="2 4" />
      <text x={(LEFT + divider) / 2} y={14} textAnchor="middle" fill="#6f6f6f" fontSize="10">T-BILLS</text>
      <text x={(divider + W - RIGHT) / 2} y={14} textAnchor="middle" fill="#6f6f6f" fontSize="10">GOVT BONDS</text>
      {segments.map(([a, b]) => (
        <line key={`${a.tenor}-${b.tenor}`} x1={slotX(a.tenor_order - 1)} y1={y(Number(a.yield_rate))} x2={slotX(b.tenor_order - 1)}
          y2={y(Number(b.yield_rate))} stroke="#8a6a1a" strokeWidth="1" strokeDasharray="4 3" />
      ))}
      {CURVE_TENORS.map((tenor, i) => (
        <text key={tenor} x={slotX(i)} y={H - BOTTOM + 15} textAnchor="middle" fontSize="11" fill={missingSet.has(tenor) ? "#555" : "#bdbdbd"}>{tenor}</text>
      ))}
      {missing.map((tenor) => (
        <text key={`m-${tenor}`} x={slotX(CURVE_TENORS.indexOf(tenor))} y={H - BOTTOM - 8} textAnchor="middle" fontSize="9" fill="#555">n/a</text>
      ))}
      {points.map((p) => {
        const cx = slotX(p.tenor_order - 1), cy = y(Number(p.yield_rate));
        const tip = `${p.tenor} · ${formatFixed(p.yield_rate, 4)}% · auction ${formatDate(p.auction_date)} · tender ${p.tender_number} · ${p.age_days} days before as-of`;
        return (
          <g key={p.tenor}>
            {p.instrument_type === "treasury_bill"
              ? <circle cx={cx} cy={cy} r="5" fill={BILL}><title>{tip}</title></circle>
              : <rect x={cx - 5} y={cy - 5} width="10" height="10" fill={BOND}><title>{tip}</title></rect>}
            <text x={cx} y={cy - 10} textAnchor="middle" fontSize="11" fill="#e6e6e6">{formatFixed(p.yield_rate, 2)}</text>
          </g>
        );
      })}
    </svg>
  );
}
