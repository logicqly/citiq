import type { ReactNode } from "react";

// ── Origo mark ────────────────────────────────────────────────────────────────

export function OrigoMark({ size }: { size?: number }) {
  const style = size ? { width: size, height: size, flex: `0 0 ${size}px` } : undefined;
  return (
    <svg viewBox="0 0 500 500" fill="none" xmlns="http://www.w3.org/2000/svg" style={style}>
      <g transform="translate(-262,-262)">
        <g transform="translate(0,1024) scale(0.1,-0.1)" fill="currentColor">
          <path d="M4955 7244 c-16 -2 -72 -9 -124 -15 -197 -23 -454 -100 -648 -196 -556 -273 -967 -778 -1124 -1378 -39 -150 -58 -288 -66 -482 l-6 -163 261 0 262 0 0 115 0 115 -145 0 -146 0 6 53 c68 599 359 1087 846 1415 252 171 520 264 877 306 l52 7 0 -204 0 -204 -67 -7 c-165 -15 -394 -100 -555 -206 -333 -217 -551 -543 -640 -954 -30 -139 -33 -493 -5 -631 93 -458 372 -841 757 -1039 115 -60 306 -123 417 -139 l88 -13 5 -315 5 -314 165 1 c447 3 857 146 1235 430 249 186 461 440 610 728 151 291 226 592 229 921 l1 160 -257 3 -258 2 0 -115 0 -115 139 0 c79 0 142 -4 146 -10 17 -27 -36 -312 -89 -475 -127 -394 -409 -760 -766 -995 -264 -173 -530 -265 -877 -303 l-43 -5 0 204 0 204 35 0 c54 0 179 28 280 62 479 163 833 574 946 1096 30 140 37 418 15 571 -53 358 -237 704 -488 919 -75 64 -214 159 -283 194 -134 68 -371 138 -467 138 l-38 0 -2 318 -3 317 -125 1 c-69 1 -138 0 -155 -2z m447 -888 c535 -137 898 -635 898 -1235 0 -509 -262 -952 -685 -1157 -490 -237 -1082 -92 -1411 345 -113 151 -191 324 -236 522 -31 139 -33 426 -4 559 61 284 166 484 350 666 181 180 387 284 641 324 107 16 337 4 447 -24z" />
          <path d="M6867 7243 c-4 -3 -7 -91 -7 -195 l0 -188 195 0 195 0 0 195 0 195 -188 0 c-104 0 -192 -3 -195 -7z" />
          <path d="M5450 7107 l0 -115 48 -7 c80 -12 272 -71 372 -114 435 -189 791 -540 985 -970 49 -110 110 -296 127 -389 7 -35 17 -66 23 -68 6 -3 59 -3 117 -2 l105 3 -23 111 c-49 239 -168 526 -301 729 -261 396 -650 700 -1083 848 -97 33 -255 74 -320 83 l-50 7 0 -116z" />
          <path d="M3024 4749 c4 -28 20 -103 37 -166 167 -649 656 -1199 1289 -1447 106 -42 309 -100 388 -111 l52 -7 0 114 0 115 -82 17 c-470 101 -900 399 -1178 816 -117 174 -211 394 -263 615 l-24 100 -113 3 -113 3 7 -52z" />
        </g>
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
// pending -> "In review at Origo", approved -> "In production",
// implemented -> "Published".
export const LIFE_CLIENT: Record<string, { tone: "" | "good" | "warn" | "bad"; label: string }> = {
  pending: { tone: "warn", label: "In review at Origo" },
  revision_requested: { tone: "warn", label: "In review at Origo" },
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
