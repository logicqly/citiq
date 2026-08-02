// The 15 client-display fields — single source of truth for both the global
// "Client display defaults" panel (Global Settings) and the per-client "Client
// display" override panel (Client > Settings). Keys mirror the client app's
// flag names and the backend's DEFAULT_DISPLAY_CONFIG.

export type DisplayConfig = Record<string, boolean>;

export interface DisplayField {
  key: string;
  label: string;
  // Nested under the field above it (model IDs under by-platform, raw responses
  // under prompts, run IDs under run history) — rendered indented.
  sub?: boolean;
}

export const DISPLAY_FIELDS: DisplayField[] = [
  { key: "score", label: "Visibility score" },
  { key: "trend", label: "Citation trend" },
  { key: "quality", label: "Citation quality" },
  { key: "sov", label: "Share of voice" },
  { key: "platforms", label: "By-platform results" },
  { key: "model_ids", label: "Model IDs", sub: true },
  { key: "prompts", label: "Prompt-level results" },
  { key: "responses", label: "Raw AI responses", sub: true },
  { key: "runs", label: "Run history" },
  { key: "run_ids", label: "Run IDs", sub: true },
  { key: "recs", label: "Recommendations tab" },
  { key: "cost", label: "Cost & usage" },
  { key: "status", label: "Run status & failures" },
  { key: "duration", label: "Run duration" },
  { key: "progress", label: "Progress indicators" },
];

// Defaults per the 20 Jul decisions: cost, recommendations, run status/failures,
// duration, progress, model IDs and run IDs are hidden from clients by default.
export const DEFAULT_DISPLAY_CONFIG: DisplayConfig = {
  score: true,
  trend: true,
  quality: true,
  sov: true,
  platforms: true,
  model_ids: false,
  prompts: true,
  responses: true,
  runs: true,
  run_ids: false,
  recs: false,
  cost: false,
  status: false,
  duration: false,
  progress: false,
};

export function resolveDisplayConfig(stored: DisplayConfig | null | undefined): DisplayConfig {
  return { ...DEFAULT_DISPLAY_CONFIG, ...(stored ?? {}) };
}
