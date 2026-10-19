/** Small dependency-free line chart (SVG) for station and dam time series. */

export interface ChartSeries {
  label: string;
  color: string;
  points: [number, number][]; // [x, y]
  dashed?: boolean;
  width?: number;
}

export interface ChartRef {
  y: number;
  label: string;
  color: string;
}

interface Props {
  series: ChartSeries[];
  refs?: ChartRef[];
  /** Shaded band between two curves drawn on the same x values (e.g. a dam's lower and upper rule curve). */
  band?: { lower: [number, number][]; upper: [number, number][]; color: string; label: string };
  xTicks: { x: number; label: string }[];
  unit: string;
  ariaLabel: string;
  height?: number;
}

const W = 340;
const PAD = { left: 44, right: 8, top: 10, bottom: 22 };
const fmt = new Intl.NumberFormat("th-TH", { maximumFractionDigits: 1 });

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

export default function LineChart({ series, refs = [], band, xTicks, unit, ariaLabel, height = 170 }: Props) {
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return <p className="chart-empty">ยังไม่มีข้อมูลพอสำหรับกราฟ</p>;
  const xs = all.map((p) => p[0]);
  const ys = [...all.map((p) => p[1]), ...refs.map((r) => r.y), ...(band ? [...band.lower, ...band.upper].map((p) => p[1]) : [])];
  const x0 = Math.min(...xs, ...xTicks.map((t) => t.x));
  const x1 = Math.max(...xs, ...xTicks.map((t) => t.x));
  let y0 = Math.min(...ys);
  let y1 = Math.max(...ys);
  const padY = (y1 - y0) * 0.08 || Math.abs(y1) * 0.1 || 1;
  y0 -= padY;
  y1 += padY;
  const H = height;
  const sx = (x: number) => PAD.left + ((x - x0) / (x1 - x0 || 1)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => H - PAD.bottom - ((y - y0) / (y1 - y0 || 1)) * (H - PAD.top - PAD.bottom);
  const path = (pts: [number, number][]) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  const yTicks = niceTicks(y0, y1);

  return (
    <figure className="line-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} className="chart-grid" />
            <text x={PAD.left - 5} y={sy(t) + 3} textAnchor="end" className="chart-tick">{fmt.format(t)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t.x} x={sx(t.x)} y={H - 6} textAnchor="middle" className="chart-tick">{t.label}</text>
        ))}
        {band && band.lower.length > 1 && (
          <path d={path(band.upper) + band.lower.slice().reverse().map((p) => `L${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("") + "Z"}
            fill={band.color} opacity={0.18} />
        )}
        {refs.map((r) => (
          <g key={r.label}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(r.y)} y2={sy(r.y)} stroke={r.color} strokeDasharray="4 3" strokeWidth={1.2} />
            <text x={W - PAD.right - 2} y={sy(r.y) - 3} textAnchor="end" className="chart-ref" fill={r.color}>{r.label}</text>
          </g>
        ))}
        {series.map((s) => s.points.length > 1 && (
          <path key={s.label} d={path(s.points)} fill="none" stroke={s.color} strokeWidth={s.width ?? 2}
            strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        <text x={4} y={PAD.top + 2} className="chart-tick">{unit}</text>
      </svg>
      <figcaption className="chart-legend">
        {series.map((s) => <span key={s.label}><i style={{ background: s.color, opacity: s.dashed ? 0.6 : 1 }} />{s.label}</span>)}
        {band && <span><i style={{ background: band.color, opacity: 0.35 }} />{band.label}</span>}
      </figcaption>
    </figure>
  );
}
