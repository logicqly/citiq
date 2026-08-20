import type { ReactNode } from "react";

// ── Citiq mark ────────────────────────────────────────────────────────────────

// Brand orange. Fixed hexes rather than tokens: the mark carries the same colour
// in both themes, so it must not resolve through the ink scale.
const BRAND = "#F06922";
const BRAND_DEEP = "#EF6623";

// The Citiq mark in brand colour, for the dashboard header.
// Source: docs/brand/citiq-colored-logo.svg (same geometry as CitiqMark below,
// with the brand fills baked in instead of currentColor).
export function CitiqLogo({ size }: { size?: number }) {
  const style = size ? { width: size, height: size, flex: `0 0 ${size}px` } : undefined;
  return (
    <svg viewBox="0 0 131.01 127.02" fill="none" xmlns="http://www.w3.org/2000/svg" style={style}>
      <path fill={BRAND} d="M58.48.26c3.81-.17,8.72-.47,13.28-.06,25.65,2.66,46.92,19.91,53.3,45.53,4.25,17.05,1.81,34.93-7.2,50.43l-15.81-16.17c8.45-21.14,0-46.19-20.78-54.49-12.9-5.15-27.82-4.54-39.78,2.75-15,9.14-21.65,26.35-18.97,43.43,2.27,14.52,11.14,26.21,24.58,31.32-.09,4.35-6.12,2.17-7.66,4.74-1.14,1.9-.74,4.3-.94,6.39-2.79,1.05-5.61.99-7.92,2.98,1.12,1.45,2.36,2.98,3.28,3.45-12.11-5.63-21.44-15.37-27.36-27.67C-5.82,67.28-.33,36.34,20.48,16.84,31.05,6.93,44.57,1.72,58.48.26Z" />
      <polygon fill={BRAND_DEEP} points="131.01 124.89 103.5 125.23 57.33 77.29 84.81 77.03 131.01 124.89" />
      <path fill={BRAND} d="M33.86,120.57c-.92-.47-5.03-2.4-6.14-3.85,2.31-2,4.21-4.76,6.99-5.81.21-2.09,1.09-4.22,2.23-6.12,1.54-2.57,10.07,2.57,10.16-1.78,6.9,2.62,14.56,3.1,22.17,1.95l16.76,17.58c-17.27,6.54-36.45,5.96-52.17-1.97Z" />
    </svg>
  );
}

// The monochrome Citiq mark, inheriting currentColor so it tracks the ink scale.
// Used where the surface owns the colour, such as the login card.
export function CitiqMark({ size }: { size?: number }) {
  const style = size ? { width: size, height: size, flex: `0 0 ${size}px` } : undefined;
  return (
    <svg viewBox="0 0 131.01 127.02" fill="none" xmlns="http://www.w3.org/2000/svg" style={style}>
      <g fill="currentColor">
        <path d="M58.48.26c3.81-.17,8.72-.47,13.28-.06,25.65,2.66,46.92,19.91,53.3,45.53,4.25,17.05,1.81,34.93-7.2,50.43l-15.81-16.17c8.45-21.14,0-46.19-20.78-54.49-12.9-5.15-27.82-4.54-39.78,2.75-15,9.14-21.65,26.35-18.97,43.43,2.27,14.52,11.14,26.21,24.58,31.32-.09,4.35-6.12,2.17-7.66,4.74-1.14,1.9-.74,4.3-.94,6.39-2.79,1.05-5.61.99-7.92,2.98,1.12,1.45,2.36,2.98,3.28,3.45-12.11-5.63-21.44-15.37-27.36-27.67C-5.82,67.28-.33,36.34,20.48,16.84,31.05,6.93,44.57,1.72,58.48.26Z" />
        <polygon points="131.01 124.89 103.5 125.23 57.33 77.29 84.81 77.03 131.01 124.89" />
        <path d="M33.86,120.57c-.92-.47-5.03-2.4-6.14-3.85,2.31-2,4.21-4.76,6.99-5.81.21-2.09,1.09-4.22,2.23-6.12,1.54-2.57,10.07,2.57,10.16-1.78,6.9,2.62,14.56,3.1,22.17,1.95l16.76,17.58c-17.27,6.54-36.45,5.96-52.17-1.97Z" />
      </g>
    </svg>
  );
}

// ── Platform meta ─────────────────────────────────────────────────────────────

export const PLATFORMS = [
  { id: "anthropic", label: "Anthropic", c: "var(--p-anthropic)" },
  { id: "gemini", label: "Gemini", c: "var(--p-gemini)" },
  { id: "openai", label: "OpenAI", c: "var(--p-openai)" },
  { id: "perplexity", label: "Perplexity", c: "var(--p-perplexity)" },
] as const;

export function platMeta(id: string | null | undefined) {
  const p = PLATFORMS.find((x) => x.id === (id ?? "").toLowerCase());
  return p ?? { id: id ?? "", label: id ?? "-", c: "var(--ink4)" };
}

// ── Chips ─────────────────────────────────────────────────────────────────────

export function Chip({ tone = "", live = false, children }: {
  tone?: "" | "good" | "warn" | "bad"; live?: boolean; children: ReactNode;
}) {
  return (
    <span className={`chip ${tone}${live ? " live" : ""}`}>
      <span className="d" />
      {children}
    </span>
  );
}

const RUN_CHIP: Record<string, { tone: "" | "good" | "warn" | "bad"; label: string; live?: boolean }> = {
  completed: { tone: "good", label: "Completed" },
  partial: { tone: "warn", label: "Partial" },
  failed: { tone: "bad", label: "Failed" },
  cancelled: { tone: "", label: "Cancelled" },
  pending: { tone: "", label: "Queued" },
  running: { tone: "", label: "Running", live: true },
  responses_ready: { tone: "warn", label: "Awaiting analysis" },
};

export function RunStatusChip({ status }: { status: string }) {
  const m = RUN_CHIP[status] ?? { tone: "" as const, label: status };
  return <Chip tone={m.tone} live={m.live}>{m.label}</Chip>;
}

// Client-friendly lifecycle labels (read-only view):
// pending -> "In review", approved -> "In production",
// implemented -> "Published".
export const LIFE_CLIENT: Record<string, { tone: "" | "good" | "warn" | "bad"; label: string }> = {
  pending: { tone: "warn", label: "In review" },
  revision_requested: { tone: "warn", label: "In review" },
  approved: { tone: "", label: "In production" },
  implemented: { tone: "good", label: "Published" },
};

export function LifeChip({ status }: { status: string }) {
  const m = LIFE_CLIENT[status] ?? { tone: "" as const, label: status.replace(/_/g, " ") };
  return <Chip tone={m.tone}>{m.label}</Chip>;
}

export function PriorityTag({ priority }: { priority: string }) {
  const cls = priority === "high" ? "hi" : priority === "medium" ? "md" : "";
  return <span className={`tag ${cls}`}>{priority}</span>;
}

export const REC_TYPE_LABELS: Record<string, string> = {
  content_brief: "Content brief",
  schema_markup: "Schema markup",
  llms_txt: "llms.txt",
  authority_building: "Authority",
};

export function TypeTag({ type }: { type: string }) {
  return <span className="tag">{REC_TYPE_LABELS[type] ?? type}</span>;
}

// ── Formatters ────────────────────────────────────────────────────────────────

export function pctFmt(v: number | null | undefined): string {
  if (v == null) return "-";
  return `${Math.round(v * 100)}%`;
}

export function usdFmt(v: number | null | undefined, decimals = 3): string {
  if (v == null) return "-";
  return `$${v.toFixed(decimals)}`;
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ── Charts ────────────────────────────────────────────────────────────────────

export function AreaChart({ vals, w = 520, h = 140 }: { vals: number[]; w?: number; h?: number }) {
  if (vals.length < 2) return <div className="emptystate">Not enough data yet.</div>;
  const mx = Math.max(...vals) * 1.15 || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 16 - (v / mx) * (h - 30)]);
  const line = pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", display: "block" }}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1="0" y1={(h - 16) * f} x2={w} y2={(h - 16) * f} style={{ stroke: "var(--bf)" }} />
      ))}
      <polygon points={`0,${h - 16} ${line} ${w},${h - 16}`} style={{ fill: "rgba(128,128,128,.09)" }} />
      <polyline points={line} fill="none" style={{ stroke: "var(--white)" }} strokeWidth="1.6" />
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
              {r.self && <span className="dim" style={{ fontSize: 10 }}> ({r.selfNote ?? "you"})</span>}
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

export function BarMeter({ pct, width, color }: { pct: number; width?: number; color?: string }) {
  return (
    <span className="bar" style={width ? { width, minWidth: width } : undefined}>
      <i style={{ width: `${Math.max(0, Math.min(100, pct))}%`, ...(color ? { background: color } : {}) }} />
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="emptystate">{children}</div>;
}
