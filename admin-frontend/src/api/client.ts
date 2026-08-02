import axios from "axios";
import type {
  AdminUser,
  Client,
  ClientDetail,
  ClientSummary,
  ClientUser,
  Competitor,
  KnowledgeBase,
  LoginResponse,
  PromptCategoryConfig,
  PromptDetail,
  PromptListResponse,
  RunCallExchange,
  RunCallListResponse,
  RunListResponse,
  RunMode,
  RunRead,
  RunSummaryResponse,
  ScheduleConfig,
  ScheduleFiresResponse,
  ScheduleResponse,
  SchedulerHealth,
} from "../types";

// VITE_API_URL is set at build time for Railway (baked into the bundle).
// Falls back to /api for local dev (proxied by Vite's dev server).
const BASE = import.meta.env.VITE_API_URL ?? "";

export const http = axios.create({ baseURL: BASE });

// ── Auth interceptors ─────────────────────────────────────────────────────────

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{ resolve: (v: string) => void; reject: (e: unknown) => void }> = [];

function processQueue(error: unknown, token: string | null) {
  failedQueue.forEach((p) => (error ? p.reject(error) : p.resolve(token!)));
  failedQueue = [];
}

http.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then((token) => {
            original.headers.Authorization = `Bearer ${token}`;
            return http(original);
          })
          .catch((err) => Promise.reject(err));
      }

      original._retry = true;
      isRefreshing = true;

      const refreshToken = localStorage.getItem("refresh_token");
      if (!refreshToken) {
        window.location.href = "/login";
        return Promise.reject(error);
      }

      try {
        const res = await axios.post(`${BASE}/admin/auth/refresh`, {
          refresh_token: refreshToken,
        });
        const newToken: string = res.data.access_token;
        localStorage.setItem("access_token", newToken);
        http.defaults.headers.common.Authorization = `Bearer ${newToken}`;
        processQueue(null, newToken);
        original.headers.Authorization = `Bearer ${newToken}`;
        return http(original);
      } catch (err) {
        processQueue(err, null);
        localStorage.clear();
        window.location.href = "/login";
        return Promise.reject(err);
      } finally {
        isRefreshing = false;
      }
    }
    return Promise.reject(error);
  }
);

// ── Auth ──────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (email: string, password: string) =>
    http.post<LoginResponse>("/admin/auth/login", { email, password }).then((r) => r.data),

  me: () => http.get<AdminUser>("/admin/auth/me").then((r) => r.data),
};

// ── Clients ───────────────────────────────────────────────────────────────────

export const clientsApi = {
  list: (status = "active") =>
    http.get<ClientSummary[]>("/admin/clients", { params: { status } }).then((r) => r.data),

  // Accepts either the client's UUID or its slug — the backend resolves both,
  // which is what lets client-scoped URLs use the slug end to end.
  get: (idOrSlug: string) =>
    http.get<ClientDetail>(`/admin/clients/${idOrSlug}`).then((r) => r.data),

  create: (body: { name: string; slug?: string; industry?: string; website?: string }) =>
    http.post<Client>("/admin/clients", body).then((r) => r.data),

  // Live availability check for the new-client form (and any future slug edit):
  // normalizes `value` the same way the backend does and reports whether the
  // resulting slug is free.
  checkSlug: (value: string, excludeClientId?: string) =>
    http
      .get<{ slug: string; available: boolean }>("/admin/clients/check-slug", {
        params: { value, exclude_client_id: excludeClientId || undefined },
      })
      .then((r) => r.data),

  update: async (id: string, body: { name?: string; industry?: string; website?: string; timezone?: string }) =>
    http.put<Client>(`/admin/clients/${await resolveClientId(id)}`, body).then((r) => r.data),

  setStatus: async (id: string, status: string) =>
    http.patch<Client>(`/admin/clients/${await resolveClientId(id)}/status`, { status }).then((r) => r.data),

  // Per-client "Client display" override. Saving a config detaches the client
  // from the global display defaults; reverting re-attaches it (display_config -> null).
  updateDisplay: async (id: string, config: Record<string, boolean>) =>
    http.put<Client>(`/admin/clients/${await resolveClientId(id)}/display`, { config }).then((r) => r.data),

  revertDisplay: async (id: string) =>
    http.delete<Client>(`/admin/clients/${await resolveClientId(id)}/display`).then((r) => r.data),
};

// ── Client ID resolution ──────────────────────────────────────────────────────
// Client-scoped URLs are addressed by slug (e.g. /clients/absolute-golf/overview),
// but the nested resource endpoints below (competitors, prompts, runs, ...) still
// take the client's real UUID. This resolves whichever a caller passes — a slug
// straight from the URL, or a UUID a caller already has — to the UUID those
// endpoints need. Valid UUIDs pass straight through (no network call); slugs are
// resolved once and cached for the rest of the session.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const slugToId = new Map<string, string>();

function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}

async function resolveClientId(idOrSlug: string): Promise<string> {
  if (isUuid(idOrSlug)) return idOrSlug;
  const cached = slugToId.get(idOrSlug);
  if (cached) return cached;
  const client = await clientsApi.get(idOrSlug);
  slugToId.set(idOrSlug, client.id);
  slugToId.set(client.slug, client.id);
  return client.id;
}

// ── Competitors ───────────────────────────────────────────────────────────────

export const competitorsApi = {
  list: async (clientId: string) =>
    http.get<Competitor[]>(`/admin/clients/${await resolveClientId(clientId)}/competitors`).then((r) => r.data),

  create: async (clientId: string, name: string) =>
    http
      .post<Competitor>(`/admin/clients/${await resolveClientId(clientId)}/competitors`, { name })
      .then((r) => r.data),

  bulkCreate: async (clientId: string, names: string[]) =>
    http
      .post<{ created: number; skipped: number }>(
        `/admin/clients/${await resolveClientId(clientId)}/competitors/bulk`,
        { names }
      )
      .then((r) => r.data),

  update: async (clientId: string, competitorId: string, name: string) =>
    http
      .put<Competitor>(`/admin/clients/${await resolveClientId(clientId)}/competitors/${competitorId}`, { name })
      .then((r) => r.data),

  delete: async (clientId: string, competitorId: string) =>
    http.delete(`/admin/clients/${await resolveClientId(clientId)}/competitors/${competitorId}`),
};

// ── Knowledge Base ────────────────────────────────────────────────────────────

export const knowledgeBaseApi = {
  get: async (clientId: string) =>
    http.get<KnowledgeBase>(`/admin/clients/${await resolveClientId(clientId)}/knowledge-base`).then((r) => r.data),

  update: async (clientId: string, body: Partial<KnowledgeBase>) =>
    http
      .put<KnowledgeBase>(`/admin/clients/${await resolveClientId(clientId)}/knowledge-base`, body)
      .then((r) => r.data),
};

// ── Runs ──────────────────────────────────────────────────────────────────────

export const runsApi = {
  list: async (clientId: string, page = 1, perPage = 20) =>
    http
      .get<RunListResponse>(`/admin/clients/${await resolveClientId(clientId)}/runs`, {
        params: { page, per_page: perPage },
      })
      .then((r) => r.data),

  // mode "full" (default) collects and analyzes in one chained task; "staged"
  // collects responses only and parks the run at responses_ready for
  // click-by-click advancement. generateRecommendations is the pre-run toggle
  // (undefined uses the client's own default).
  trigger: async (
    clientId: string,
    mode: RunMode = "full",
    generateRecommendations?: boolean,
  ) =>
    http
      .post<RunRead>(`/admin/clients/${await resolveClientId(clientId)}/runs/trigger`, {
        mode,
        generate_recommendations: generateRecommendations,
      })
      .then((r) => r.data),

  // Staged runs: start the analysis stage for a run parked at responses_ready.
  analyze: async (clientId: string, runId: string) =>
    http.post<RunRead>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/analyze`).then((r) => r.data),

  // Generate recommendations for a completed/partial run that lacks them
  // (staged runs' third click — also retries a failed generation).
  generate: async (clientId: string, runId: string) =>
    http.post<RunRead>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/generate`).then((r) => r.data),

  // Kill switch (R4): stops an in-flight run — no new API spend after this.
  // Also discards a staged run parked at responses_ready.
  cancel: async (clientId: string, runId: string) =>
    http.post<RunRead>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/cancel`).then((r) => r.data),

  get: async (clientId: string, runId: string) =>
    http
      .get<RunSummaryResponse>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}`)
      .then((r) => r.data),

  getPrompts: async (clientId: string, runId: string) =>
    http
      .get<PromptDetail[]>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/prompts`)
      .then((r) => r.data),

  downloadJson: async (clientId: string, runId: string) =>
    http
      .get(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/report/json`, { responseType: "blob" })
      .then((r) => r.data as Blob),

  downloadPdf: async (clientId: string, runId: string) =>
    http
      .get(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/report/pdf`, { responseType: "blob" })
      .then((r) => r.data as Blob),

  // Run call log (diagnostics): every upstream attempt with its typed
  // outcome. outcome "failed" (default) | "all" | a specific value.
  getCalls: async (clientId: string, runId: string, outcome = "failed") =>
    http
      .get<RunCallListResponse>(
        `/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/calls`,
        { params: { outcome } },
      )
      .then((r) => r.data),

  getCallExchanges: async (clientId: string, runId: string, callId: string) =>
    http
      .get<RunCallExchange[]>(
        `/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/calls/${callId}/exchanges`,
      )
      .then((r) => r.data),

  // The full per-run call log as a downloadable NDJSON file.
  downloadLog: async (clientId: string, runId: string) =>
    http
      .get(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/log.ndjson`, { responseType: "blob" })
      .then((r) => r.data as Blob),
};

// ── Prompts ───────────────────────────────────────────────────────────────────

export const promptsApi = {
  list: async (
    clientId: string,
    filters: {
      category?: string;
      is_active?: boolean;
      search?: string;
      page?: number;
      per_page?: number;
    } = {}
  ) => {
    const params = new URLSearchParams();
    if (filters.category) params.set("category", filters.category);
    if (filters.is_active !== undefined) params.set("is_active", String(filters.is_active));
    if (filters.search) params.set("search", filters.search);
    if (filters.page) params.set("page", String(filters.page));
    if (filters.per_page) params.set("per_page", String(filters.per_page));
    const id = await resolveClientId(clientId);
    return http
      .get<PromptListResponse>(`/admin/clients/${id}/prompts?${params}`)
      .then((r) => r.data);
  },

  create: async (clientId: string, text: string, category: string) =>
    http
      .post(`/admin/clients/${await resolveClientId(clientId)}/prompts`, { text, category })
      .then((r) => r.data),

  update: async (clientId: string, promptId: string, body: Record<string, unknown>) =>
    http.put(`/admin/clients/${await resolveClientId(clientId)}/prompts/${promptId}`, body).then((r) => r.data),

  deactivate: async (clientId: string, promptId: string) =>
    http.delete(`/admin/clients/${await resolveClientId(clientId)}/prompts/${promptId}`),

  activate: async (clientId: string, promptId: string) =>
    http
      .put(`/admin/clients/${await resolveClientId(clientId)}/prompts/${promptId}`, { is_active: true })
      .then((r) => r.data),

  bulkCreate: async (clientId: string, prompts: Array<{ text: string; category: string }>) =>
    http
      .post(`/admin/clients/${await resolveClientId(clientId)}/prompts/bulk`, { prompts })
      .then((r) => r.data),

  uploadCsv: async (clientId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const id = await resolveClientId(clientId);
    return http
      .post(`/admin/clients/${id}/prompts/upload-csv`, fd, {
        headers: { "Content-Type": undefined },
      })
      .then((r) => r.data);
  },
};

// ── Recommendations ───────────────────────────────────────────────────────────

export const recommendationsApi = {
  summary: async (clientId: string) =>
    http
      .get<import("../types").RecommendationSummary>("/admin/recommendations/summary", {
        params: { client_id: await resolveClientId(clientId) },
      })
      .then((r) => r.data),

  list: async (
    clientId: string,
    filters: {
      status?: string;
      type?: string;
      priority?: string;
      run_id?: string;
      prompt_id?: string;
      page?: number;
      per_page?: number;
      sort_by?: string;
      sort_order?: string;
    } = {}
  ) => {
    const params: Record<string, string | number | undefined> = {
      client_id: await resolveClientId(clientId),
      ...filters,
    };
    return http
      .get<import("../types").RecommendationListResponse>("/admin/recommendations", { params })
      .then((r) => r.data);
  },

  // Per-run / per-prompt rollup for the client Recommendations tab.
  groups: async (clientId: string, groupBy: "run" | "prompt", status?: string) =>
    http
      .get<import("../types").RecommendationGroupsResponse>("/admin/recommendations/groups", {
        params: { client_id: await resolveClientId(clientId), group_by: groupBy, status: status || undefined },
      })
      .then((r) => r.data),

  get: (id: string) =>
    http
      .get<import("../types").RecommendationDetail>(`/admin/recommendations/${id}`)
      .then((r) => r.data),

  approve: (id: string, notes?: string) =>
    http
      .post<import("../types").RecommendationDetail>(`/admin/recommendations/${id}/approve`, { notes })
      .then((r) => r.data),

  reject: (id: string, notes: string) =>
    http
      .post<import("../types").RecommendationDetail>(`/admin/recommendations/${id}/reject`, { notes })
      .then((r) => r.data),

  requestRevision: (id: string, notes: string) =>
    http
      .post<import("../types").RecommendationDetail>(`/admin/recommendations/${id}/request-revision`, {
        notes,
      })
      .then((r) => r.data),

  implement: (id: string, notes?: string) =>
    http
      .post<import("../types").RecommendationDetail>(`/admin/recommendations/${id}/implement`, { notes })
      .then((r) => r.data),
};

// ── Platform model config ─────────────────────────────────────────────────────

export interface AvailableModelsResponse {
  platforms: Record<string, string[]>;
  defaults: Record<string, string>;
}

export interface PlatformModelConfig {
  config: Record<string, string>;
}

export const platformConfigApi = {
  getAvailableModels: () =>
    http.get<AvailableModelsResponse>("/admin/platforms/models").then((r) => r.data),

  getConfig: async (clientId: string) =>
    http.get<PlatformModelConfig>(`/admin/clients/${await resolveClientId(clientId)}/platform-config`).then((r) => r.data),

  updateConfig: async (clientId: string, config: Record<string, string>) =>
    http
      .put<PlatformModelConfig>(`/admin/clients/${await resolveClientId(clientId)}/platform-config`, { config })
      .then((r) => r.data),
};

// ── Cost ─────────────────────────────────────────────────────────────────────

export const costApi = {
  getRunCosts: async (clientId: string, runId: string) =>
    http.get<import("../types").RunCostSummary>(`/admin/clients/${await resolveClientId(clientId)}/runs/${runId}/costs`).then((r) => r.data),

  getClientCostSummary: async (clientId: string) =>
    http.get<import("../types").ClientCostAverages>(`/admin/clients/${await resolveClientId(clientId)}/cost-summary`).then((r) => r.data),

  getClientRunStats: async (clientId: string, period: import("../types").RunStatsPeriod) =>
    http
      .get<import("../types").ClientRunStats>(`/admin/clients/${await resolveClientId(clientId)}/runs/stats`, { params: { period } })
      .then((r) => r.data),
};

// ── Scheduler ─────────────────────────────────────────────────────────────────

export const scheduleApi = {
  get: async (clientId: string) =>
    http.get<ScheduleResponse>(`/admin/clients/${await resolveClientId(clientId)}/schedule`).then((r) => r.data),

  fires: async (clientId: string, window: "24h" | "7d") =>
    http
      .get<ScheduleFiresResponse>(`/admin/clients/${await resolveClientId(clientId)}/schedule/fires`, {
        params: { window },
      })
      .then((r) => r.data),

  update: async (clientId: string, body: ScheduleConfig) =>
    http.put<ScheduleResponse>(`/admin/clients/${await resolveClientId(clientId)}/schedule`, body).then((r) => r.data),

  pause: async (clientId: string) =>
    http.post(`/admin/clients/${await resolveClientId(clientId)}/schedule/pause`),

  resume: async (clientId: string) =>
    http
      .post<ScheduleResponse>(`/admin/clients/${await resolveClientId(clientId)}/schedule/resume`)
      .then((r) => r.data),

  health: () =>
    http.get<SchedulerHealth>("/admin/scheduler/health").then((r) => r.data),

  pauseAll: (reason: string) =>
    http
      .post<{ paused_count: number }>("/admin/scheduler/pause-all", { reason })
      .then((r) => r.data),
};

// ── Client (dashboard) users ─────────────────────────────────────────────────

export const clientUsersApi = {
  list: async (clientId: string) =>
    http.get<ClientUser[]>(`/admin/clients/${await resolveClientId(clientId)}/users`).then((r) => r.data),

  create: async (clientId: string, body: { email: string; display_name: string; password: string; role: string }) =>
    http.post<ClientUser>(`/admin/clients/${await resolveClientId(clientId)}/users`, body).then((r) => r.data),

  setActive: async (clientId: string, userId: string, active: boolean) =>
    http.put(`/admin/clients/${await resolveClientId(clientId)}/users/${userId}`, { is_active: active }),

  resetPassword: async (clientId: string, userId: string, newPassword: string) =>
    http.post(`/admin/clients/${await resolveClientId(clientId)}/users/${userId}/reset-password`, {
      new_password: newPassword,
    }),
};

// ── Global settings (system-wide) ─────────────────────────────────────────────
// The global model config has the same shape as a client's platform-config.

export const settingsApi = {
  getModelConfig: () =>
    http.get<PlatformModelConfig>("/admin/settings/model-config").then((r) => r.data),

  updateModelConfig: (config: Record<string, string>) =>
    http
      .put<{ config: Record<string, string>; clients_updated: number }>(
        "/admin/settings/model-config",
        { config }
      )
      .then((r) => r.data),

  getVisibilityWeights: () =>
    http
      .get<{ weights: Record<string, number> }>("/admin/settings/visibility-weights")
      .then((r) => r.data),

  updateVisibilityWeights: (weights: Record<string, number>) =>
    http
      .put<{ weights: Record<string, number> }>("/admin/settings/visibility-weights", { weights })
      .then((r) => r.data),

  getPromptCategories: () =>
    http
      .get<{ categories: PromptCategoryConfig[] }>("/admin/settings/prompt-categories")
      .then((r) => r.data.categories),

  updatePromptCategories: (categories: PromptCategoryConfig[]) =>
    http
      .put<{ categories: PromptCategoryConfig[] }>("/admin/settings/prompt-categories", { categories })
      .then((r) => r.data.categories),

  // Global "Client display defaults" — applied to every inheriting client.
  getDisplayDefaults: () =>
    http.get<{ config: Record<string, boolean> }>("/admin/settings/display-defaults").then((r) => r.data.config),

  updateDisplayDefaults: (config: Record<string, boolean>) =>
    http
      .put<{ config: Record<string, boolean> }>("/admin/settings/display-defaults", { config })
      .then((r) => r.data.config),
};
