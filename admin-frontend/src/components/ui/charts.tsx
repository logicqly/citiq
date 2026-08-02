// Hand-rolled SVG charts matching the instrument-panel design system.
// All colors come from CSS tokens (set via style so var() resolves), which
// keeps every chart correct in both the dark and light themes.

export function Sparkline({ vals, w = 90, h = 26, color = "var(--ink2)" }: {
  vals: number[]; w?: number; h?: number; color?: string;
}) {
  if (vals.length < 2) {
    return (
      <svg width={w} height={h} style={{ display: "block" }}>
        <line x1="1" y1={h / 2} x2={w - 1} y2={h / 2} style={{ stroke: "var(--ink6)" }} strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }
  const mx = Math.max(...vals), mn = Math.min(...vals), rg = (mx - mn) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - 3 - ((v - mn) / rg) * (h - 6)}`).join(" ");
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <polyline points={pts} fill="none" style={{ stroke: color }} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function AreaChart({ vals, w = 560, h = 150 }: { vals: number[]; w?: number; h?: number }) {
  if (vals.length < 2) return <div className="emptystate">Not enough data yet.</div>;
  const mx = Math.max(...vals) * 1.15 || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 16 - (v / mx) * (h - 30)]);
  const line = pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
  const area = `0,${h - 16} ${line} ${w},${h - 16}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", display: "block" }}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1="0" y1={(h - 16) * f} x2={w} y2={(h - 16) * f} style={{ stroke: "var(--bf)" }} />
      ))}
      <polygon points={area} style={{ fill: "rgba(128,128,128,.09)" }} />
      <polyline points={line} fill="none" style={{ stroke: "var(--white)" }} strokeWidth="1.6" />
    </svg>
  );
}

export interface DonutSeg { v: number; c: string }

export function Donut({ segs, size = 140, hole = 44 }: { segs: DonutSeg[]; size?: number; hole?: number }) {
  const tot = segs.reduce((a, s) => a + s.v, 0) || 1;
  let a0 = -Math.PI / 2;
  const cx = size / 2, cy = size / 2, r = size / 2 - 4;
  const paths = segs.filter((s) => s.v > 0).map((s, i) => {
    const a1 = a0 + (s.v / tot) * Math.PI * 2;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const d = `M${cx},${cy} L${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 ${large} 1 ${x1.toFixed(1)},${y1.toFixed(1)} Z`;
    a0 = a1;
    return <path key={i} d={d} style={{ fill: s.c }} opacity=".92" />;
  });
  return (
    <svg width={size} height={size} style={{ display: "block" }}>
      {paths}
      <circle cx={cx} cy={cy} r={hole} style={{ fill: "var(--s2)" }} />
    </svg>
  );
}

export interface HBarRow { label: string; v: number; right: string; self?: boolean; selfNote?: string }

export function HBars({ rows, max }: { rows: HBarRow[]; max?: number }) {
  const mx = max ?? (Math.max(...rows.map((r) => r.v)) || 1);
  return (
    <>
      {rows.map((r) => (
        <div key={r.label} style={{ marginBottom: 11 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, marginBottom: 5 }}>
            <span style={r.self ? { fontWeight: 700 } : undefined}>
              {r.label}
              {r.self && <span className="dim" style={{ fontSize: 10 }}> ({r.selfNote ?? "client"})</span>}
            </span>
            <span className="mono dim2">{r.right}</span>
          </div>
          <span className="bar" style={{ display: "block" }}>
            <i style={{ width: `${Math.min(100, Math.round((r.v / mx) * 100))}%`, ...(r.self ? { background: "var(--good)" } : {}) }} />
          </span>
        </div>
      ))}
    </>
  );
}

export function Ring({ value, size = 110, stroke = 9, color = "var(--good)", label }: {
  value: number; size?: number; stroke?: number; color?: string; label?: string;
}) {
  const r = (size - stroke - 5) / 2;
  const circ = 2 * Math.PI * r;
  const c = size / 2;
  return (
    <div className="ringwrap" style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={c} cy={c} r={r} fill="none" style={{ stroke: "var(--s4)" }} strokeWidth={stroke} />
        <circle
          cx={c} cy={c} r={r} fill="none" style={{ stroke: color }} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${((Math.max(0, Math.min(100, value)) / 100) * circ).toFixed(1)} ${circ.toFixed(1)}`}
        />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <b className="mono" style={{ fontSize: 30, fontWeight: 500, letterSpacing: "-.03em" }}>{Math.round(value)}</b>
        {label && <span className="mono" style={{ fontSize: 8.5, color: "var(--ink4)", letterSpacing: ".18em", marginTop: 2 }}>{label}</span>}
      </div>
    </div>
  );
}
