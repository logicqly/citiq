import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import type { RecommendationPriority, RecommendationStatus, RecommendationType } from "../../types";

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

export function PlatformCell({ platform }: { platform: string | null | undefined }) {
  if (!platform) return <span className="dim">-</span>;
  const p = platMeta(platform);
  return (
    <span className="plat-cell">
      <span className="pd" style={{ background: p.c }} />
      {p.label}
    </span>
  );
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

// Lifecycle language (16 Jul meeting): For review, In progress, Published, Archived.
// Engine mapping: pending -> For review, approved -> In progress,
// implemented -> Published, rejected -> Archived bucket.
export const LIFE_LABELS: Record<RecommendationStatus, string> = {
  pending: "For review",
  approved: "In progress",
  implemented: "Published",
  rejected: "Rejected",
  revision_requested: "Revision requested",
};

const LIFE_CHIP: Record<RecommendationStatus, "" | "good" | "warn" | "bad"> = {
  pending: "warn",
  approved: "",
  implemented: "good",
  rejected: "bad",
  revision_requested: "warn",
};

export function LifeChip({ status }: { status: RecommendationStatus }) {
  return <Chip tone={LIFE_CHIP[status] ?? ""}>{LIFE_LABELS[status] ?? status}</Chip>;
}

export function ClientStatusChip({ status }: { status: string }) {
  if (status === "active") return <Chip tone="good">Active</Chip>;
  if (status === "paused") return <Chip tone="warn">Paused</Chip>;
  return <Chip>{status.charAt(0).toUpperCase() + status.slice(1)}</Chip>;
}

export function PriorityTag({ priority }: { priority: RecommendationPriority }) {
  const cls = priority === "high" ? "hi" : priority === "medium" ? "md" : "lo";
  return <span className={`tag ${cls}`}>{priority}</span>;
}

export const REC_TYPE_LABELS: Record<RecommendationType, string> = {
  content_brief: "Content brief",
  schema_markup: "Schema markup",
  llms_txt: "llms.txt",
  authority_building: "Authority",
};

export function TypeTag({ type }: { type: RecommendationType }) {
  return <span className="tag">{REC_TYPE_LABELS[type] ?? type}</span>;
}

// ── Controls ──────────────────────────────────────────────────────────────────

export function TSwitch({ on, onToggle, disabled, label }: {
  on: boolean; onToggle: () => void; disabled?: boolean; label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label ?? (on ? "Disable" : "Enable")}
      disabled={disabled}
      onClick={onToggle}
      className={`tswitch${on ? " on" : ""}`}
    >
      <i />
    </button>
  );
}

export function PillRow<T extends string>({ options, value, onChange }: {
  options: Array<{ value: T; label: ReactNode }>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="pillrow">
      {options.map((o) => (
        <button key={o.value} type="button" className={`pi${value === o.value ? " on" : ""}`} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder, style }: {
  value: string; onChange: (v: string) => void; placeholder: string; style?: React.CSSProperties;
}) {
  return (
    <div className="searchbox" style={style}>
      <SearchRoundedIcon className="dim" style={{ fontSize: 15 }} />
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function BarMeter({ pct, width, tone }: { pct: number; width?: number; tone?: "good" | "warn" }) {
  return (
    <span className={`bar${tone ? ` ${tone}` : ""}`} style={width ? { width, minWidth: width } : undefined}>
      <i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </span>
  );
}

// ── Overlays ──────────────────────────────────────────────────────────────────

export function Modal({ onClose, children, wide }: { onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="modal-wrap">
      <div className="scrim" onClick={onClose} />
      <div className={`modal${wide ? " lg" : ""}`}>{children}</div>
    </div>
  );
}

export function Drawer({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div className="drawer-wrap">
      <div className="scrim" onClick={onClose} />
      <div className="drawer">{children}</div>
    </div>
  );
}

// ── Confirm dialog ────────────────────────────────────────────────────────────
// Promise-based replacement for window.confirm, styled like every other modal.
//   const confirm = useConfirm();
//   if (await confirm({ title, message, danger: true })) { ... }

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(() => Promise.resolve(false));

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ opts: ConfirmOptions; resolve: (v: boolean) => void } | null>(null);

  const confirm = useCallback<ConfirmFn>(
    (opts) => new Promise<boolean>((resolve) => setPending({ opts, resolve })),
    [],
  );

  function close(result: boolean) {
    pending?.resolve(result);
    setPending(null);
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <Modal onClose={() => close(false)}>
          <h3 style={pending.opts.danger ? { color: "var(--bad)" } : undefined}>{pending.opts.title}</h3>
          <div className="ms">{pending.opts.message}</div>
          <div className="macts">
            <button className="btn" onClick={() => close(false)}>
              {pending.opts.cancelLabel ?? "Cancel"}
            </button>
            <button
              className={pending.opts.danger ? "btn danger" : "btn pri"}
              autoFocus
              onClick={() => close(true)}
            >
              {pending.opts.confirmLabel ?? "Confirm"}
            </button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}

// ── Toasts ────────────────────────────────────────────────────────────────────

interface ToastItem { id: number; msg: string; kind: "ok" | "err" }

const ToastContext = createContext<(msg: string, kind?: "ok" | "err") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const push = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3400);
  }, []);

  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.kind === "err" ? " err" : ""}`}>
            <span className="tic">
              {t.kind === "err"
                ? <ErrorOutlineRoundedIcon style={{ fontSize: 15 }} />
                : <CheckRoundedIcon style={{ fontSize: 15 }} />}
            </span>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

// ── Misc ──────────────────────────────────────────────────────────────────────

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="emptystate">{children}</div>;
}

export function getInitials(name: string): string {
  return name.split(/\s+/).map((w) => w[0]).join("").toUpperCase().slice(0, 2);
}

export function pctFmt(v: number | null | undefined): string {
  if (v == null) return "-";
  return `${(v * 100).toFixed(v > 0 && v < 0.05 ? 1 : 0)}%`;
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

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "-";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
