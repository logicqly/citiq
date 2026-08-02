import type { DashboardSummary, PromptDetail, RunSummaryResponse } from "./types";

// In production VITE_API_URL is the full API base (baked in at build time).
// In local dev it's empty — Vite proxy forwards to localhost:8000.
const BASE = (import.meta.env.VITE_API_URL ?? "") as string;

function getToken(): string | null {
  return localStorage.getItem("client_access_token");
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

async function apiDownload(path: string): Promise<Blob> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.blob();
}

// ── Dashboard API (JWT-scoped — no client_id in URL) ─────────────────────────

export interface RunListItem {
  id: string;
  display_id: string | null;
  status: string;
  total_prompts: number;
  completed_prompts: number;
  created_at: string;
  updated_at: string | null;
  overall_citation_rate: number | null;
  cost_usd: number | null;
  // Actual working ms per phase; staged runs idle between admin clicks, so
  // Duration sums these instead of updated_at - created_at when present.
  phase_timings?: {
    monitoring_ms?: number;
    analysis_ms?: number;
    generation_ms?: number;
  } | null;
}

interface RunCostPhase {
  tokens?: number;
  cost_usd: number;
  api_calls: number;
  duration_ms?: number | null;
}

export interface RunCostSummary {
  total_tokens: number | null;
  total_cost_usd: number | null;
  breakdown: {
    monitoring: RunCostPhase | null;
    analysis: RunCostPhase | null;
    generation: RunCostPhase | null;
  };
  cost_by_platform: Record<string, { tokens: number; cost_usd: number; api_calls: number }>;
}

export interface ClientCostAverages {
  total_runs: number;
  avg_tokens_per_run: number | null;
  avg_cost_per_run_usd: number | null;
  total_cost_all_time_usd: number | null;
  cost_trend: Array<{ run_id: string; date: string; cost_usd: number; tokens: number }>;
}

export interface RunListResponse {
  runs: RunListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface Competitor {
  id: string;
  name: string;
}

// ── Recommendations ───────────────────────────────────────────────────────────

export interface ClientRecommendationListItem {
  id: string;
  type: string;
  status: string;
  priority: string;
  title: string;
  platform: string | null;
  target_query: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClientHistoryItem {
  id: string;
  old_status: string | null;
  new_status: string;
  actor: string;
  created_at: string;
}

export interface ClientRecommendationDetail extends ClientRecommendationListItem {
  content: Record<string, unknown>;
  history: ClientHistoryItem[];
}

export interface ClientRecommendationListResponse {
  items: ClientRecommendationListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ClientRecommendationSummary {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  by_priority: Record<string, number>;
  pending_high_priority: number;
}

export const dashboard = {
  getSummary: () => apiFetch<DashboardSummary>("/client/dashboard/summary"),
  getRuns: (page = 1) => apiFetch<RunListResponse>(`/client/dashboard/runs?page=${page}`),
  getLatestRun: () => apiFetch<RunSummaryResponse | null>("/client/dashboard/runs/latest"),
  getRunDetail: (runId: string) => apiFetch<RunSummaryResponse>(`/client/dashboard/runs/${runId}`),
  getRunPrompts: (runId: string) => apiFetch<PromptDetail[]>(`/client/dashboard/runs/${runId}/prompts`),
  getCompetitors: () => apiFetch<Competitor[]>("/client/dashboard/competitors"),
  downloadRunJson: (runId: string) => apiDownload(`/client/dashboard/runs/${runId}/report/json`),
  downloadRunPdf: (runId: string) => apiDownload(`/client/dashboard/runs/${runId}/report/pdf`),
  getRunCosts: (runId: string) => apiFetch<RunCostSummary>(`/client/dashboard/runs/${runId}/costs`),
  getCostSummary: () => apiFetch<ClientCostAverages>("/client/dashboard/cost-summary"),
};

export const recommendations = {
  getSummary: () => apiFetch<ClientRecommendationSummary>("/client/recommendations/summary"),
  list: (params?: { page?: number; type?: string; status?: string; priority?: string }) => {
    const q = new URLSearchParams();
    if (params?.page) q.set("page", String(params.page));
    if (params?.type) q.set("type", params.type);
    if (params?.status) q.set("status", params.status);
    if (params?.priority) q.set("priority", params.priority);
    const qs = q.toString();
    return apiFetch<ClientRecommendationListResponse>(`/client/recommendations${qs ? `?${qs}` : ""}`);
  },
  get: (id: string) => apiFetch<ClientRecommendationDetail>(`/client/recommendations/${id}`),
};

// kept for backward compat with any remaining imports
export const api = {
  getLatestRun: (_clientId: string) => dashboard.getLatestRun(),
  getRun: (runId: string) => dashboard.getRunDetail(runId),
  getRunPrompts: (runId: string) => dashboard.getRunPrompts(runId),
};
