// Client-display flags — mirrors the backend's DEFAULT_DISPLAY_CONFIG and the
// admin console's field list. Every widget, nav tab and table column in this
// app renders off these booleans. The effective values arrive with the
// authenticated user (resolved server-side from the client's override or the
// global defaults); this module supplies the fallback + a resolver so a missing
// or partial config still renders sensibly.

export type DisplayConfig = Record<string, boolean>;

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
