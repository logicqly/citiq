// ── Auth ──────────────────────────────────────────────────────────────────────

export type AdminRole = "super_admin" | "geo_lead" | "analyst";

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: AdminRole;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: AdminUser;
}

// ── Clients ───────────────────────────────────────────────────────────────────

export type ClientStatus = "active" | "paused" | "archived";
export type ScheduleCadence = "hourly" | "daily" | "weekly" | "manual";

export interface Client {
  id: string;
  name: string;
  slug: string;
  industry: string | null;
  website: string | null;
  status: ClientStatus;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  timezone: string;           // IANA timezone, e.g. "Asia/Colombo"
  // Default for the pre-run recommendations toggle. A run captures the value
  // it was triggered with, so changing this never affects a run in flight.
  auto_generate_recommendations: boolean;
  // Schedule fields (always returned by the API)
  schedule_enabled: boolean;
  schedule_cadence: ScheduleCadence;
  schedule_hour: number;
  schedule_minute: number;
  schedule_day_of_week: number | null;
  next_scheduled_run_at: string | null;
  last_scheduled_run_at: string | null;
  // Per-client AI model overrides
  platform_model_config: Record<string, string> | null;
  // Platforms this client is monitored on. null = all of them.
  enabled_platforms: string[] | null;
  // Per-client "Client display" override. null = following the global display
  // defaults; an object = customised/detached.
  display_config: Record<string, boolean> | null;
  // Brand logo state. The bytes live behind /admin/clients/{id}/logo, never in
  // this payload; logo_updated_at doubles as the cache-buster for previews.
  has_logo: boolean;
  logo_mime: string | null;
  logo_filename: string | null;
  logo_updated_at: string | null;
}

export interface ClientLogo {
  has_logo: boolean;
  logo_mime: string | null;
  logo_filename: string | null;
  logo_updated_at: string | null;
}

export interface ClientSummary extends Client {
  total_prompts: number;
  total_competitors: number;
  last_run_at: string | null;
  last_run_status: string | null;
  latest_citation_rate: number | null;
  /** Per-run citation rates (0..1) for recent results-bearing runs, oldest first. */
  citation_history: number[];
  schedule_enabled: boolean;
  schedule_cadence: ScheduleCadence;
  next_scheduled_run_at: string | null;
}

export interface KnowledgeBase {
  id: string;
  client_id: string;
  brand_profile: Record<string, unknown>;
  target_audience: Record<string, unknown>;
  brand_voice: Record<string, unknown>;
  industry_context: Record<string, unknown>;
  version: number;
  updated_at: string;
}

export interface ClientDetail extends Client {
  knowledge_base: KnowledgeBase | null;
  total_prompts: number;
  total_competitors: number;
}

// ── Client (dashboard) users ─────────────────────────────────────────────────

export interface ClientUser {
  id: string;
  client_id: string;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

// ── Competitors ───────────────────────────────────────────────────────────────

export interface Competitor {
  id: string;
  name: string;
  client_id: string;
}

// ── Prompts ───────────────────────────────────────────────────────────────────

// Categories are admin-managed (see GlobalSettings), not a fixed enum.
// A prompt's category is the category name, or "" for no category.
export type PromptCategory = string;

export interface PromptCategoryConfig {
  name: string;
  color: string;
  description?: string;
}

// "partial": terminal with results, but some monitoring calls or analyses
// were dropped — never displayed as "completed".
// "cancelled": an admin pulled the kill switch on an in-flight run.
// "responses_ready": staged run parked after monitoring — responses are
// collected, analysis awaits an explicit click (or cancel to discard).
export type RunStatus =
  "pending" | "running" | "responses_ready" | "completed" | "partial" | "failed" | "cancelled";

// Trigger modes: "full" runs everything in one task; "staged" collects
// responses only, then analysis/generation advance one click at a time.
export type RunMode = "full" | "staged";

// ── Scheduler ─────────────────────────────────────────────────────────────────

export type SchedulerRunStatus = "enqueued" | "started" | "completed" | "failed" | "skipped";

export interface ScheduleConfig {
  schedule_enabled: boolean;
  schedule_cadence: ScheduleCadence;
  schedule_hour: number;
  schedule_minute: number;
  schedule_day_of_week: number | null;
}

export interface SchedulerRunItem {
  id: string;
  run_id: string | null;
  triggered_at: string;
  status: SchedulerRunStatus;
  cadence: ScheduleCadence;
  error_message: string | null;
  retry_count: number;
  created_at: string;
}

export interface ScheduleResponse extends ScheduleConfig {
  last_scheduled_run_at: string | null;
  next_scheduled_run_at: string | null;
  is_due_now: boolean;
  recent_runs: SchedulerRunItem[];
}

// One "fire" = a single execution (run) of the client's monitoring.
export interface ScheduleFire {
  id: string;
  timestamp: string;          // ISO — run start
  duration_seconds: number;   // run length (end - start)
  status: RunStatus;
}

export interface ScheduleFiresResponse {
  window: "24h" | "7d";
  fires: ScheduleFire[];      // oldest → newest
}

export interface SchedulerHealth {
  last_tick_at: string | null;
  last_tick_age_seconds: number | null;
  is_healthy: boolean;
  last_tick_clients_evaluated: number | null;
  last_tick_runs_enqueued: number | null;
  consecutive_failures: number;
  last_error: string | null;
  active_clients_count: number;
  scheduled_runs_today: Record<string, number>;
}
export type Platform = "perplexity" | "openai" | "anthropic" | "gemini";
export type Prominence = "primary" | "secondary" | "mentioned" | "not_cited";
export type Sentiment = "positive" | "neutral" | "negative" | "not_cited";
export type CitationOpportunity = "high" | "medium" | "low";
export type CitationType = "recommended" | "mentioned" | "negative" | "hollow" | "not_cited";

export interface Prompt {
  id: string;
  client_id: string;
  text: string;
  category: PromptCategory;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptListResponse {
  items: Prompt[];
  total: number;
  page: number;
  per_page: number;
}

// ── Runs ──────────────────────────────────────────────────────────────────────

// Actual working ms per phase; staged runs idle between clicks, so Duration
// sums these instead of updated_at - created_at when present.
export interface PhaseTimings {
  monitoring_ms?: number;
  analysis_ms?: number;
  generation_ms?: number;
  // Elapsed time of collection and analysis together. They overlap (each
  // response is analyzed as it arrives), so the two above must not be summed
  // when this is present.
  collection_analysis_ms?: number;
}

export interface RunRead {
  id: string;
  client_id: string;
  status: RunStatus;
  // Post-monitoring phase marker: progress full + status "running" +
  // generation "pending" → analysis phase; "running" → generating recs.
  generation_status?: "pending" | "running" | "completed" | "failed" | "skipped";
  total_prompts: number;
  completed_prompts: number;
  error_message: string | null;
  phase_timings?: PhaseTimings | null;
  created_at: string;
  updated_at: string;
}

export interface RunSummaryItem {
  id: string;
  display_id: string | null;
  status: string;
  generation_status?: string | null;
  total_prompts: number;
  completed_prompts: number;
  created_at: string;
  updated_at: string;
  overall_citation_rate: number | null;
  cost_usd: number | null;
  phase_timings?: PhaseTimings | null;
}

// ── Cost ──────────────────────────────────────────────────────────────────────

export interface RunCostBreakdownPhase {
  tokens?: number;
  // Per-direction split. 0 for runs whose rows predate migration 0029.
  input_tokens?: number;
  output_tokens?: number;
  cost_usd: number;
  api_calls: number;
  // Actual working time of the phase in ms (recorded on the run).
  duration_ms?: number | null;
}

export interface RunCostByPlatform {
  tokens: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd: number;
  api_calls: number;
}

export interface RunCostSummary {
  total_tokens: number | null;
  total_input_tokens?: number | null;
  total_output_tokens?: number | null;
  total_cost_usd: number | null;
  breakdown: {
    monitoring: RunCostBreakdownPhase | null;
    analysis: RunCostBreakdownPhase | null;
    generation: RunCostBreakdownPhase | null;
  };
  cost_by_platform: Record<string, RunCostByPlatform>;
}

export interface CostTrendPoint {
  run_id: string;
  date: string;
  cost_usd: number;
  tokens: number;
}

export interface ClientCostAverages {
  total_runs: number;
  avg_tokens_per_run: number | null;
  avg_cost_per_run_usd: number | null;
  total_cost_all_time_usd: number | null;
  cost_trend: CostTrendPoint[];
}

export type RunStatsPeriod = "today" | "7d" | "30d" | "90d";

export interface ClientRunStats {
  period: RunStatsPeriod;
  total_cost_usd: number;
  prior_total_cost_usd: number;
  p95_duration_seconds: number | null;
  run_count: number;
}

export interface RunListResponse {
  items: RunSummaryItem[];
  total: number;
  page: number;
  per_page: number;
}

// ── Run call log (per-attempt diagnostics) ────────────────────────────────────

export interface RunCallItem {
  id: string;
  phase: "monitoring" | "analysis" | "generation";
  outcome: string;
  attempt: number;
  prompt_id: string | null;
  prompt_text: string | null;
  response_id: string | null;
  response_platform: string | null;
  rec_type: string | null;
  platform: string | null;
  model: string | null;
  error_type: string | null;
  error_detail: string | null;
  http_status: number | null;
  latency_ms: number | null;
  tokens_used: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  has_exchanges: boolean;
  created_at: string;
}

export interface RunCallListResponse {
  items: RunCallItem[];
  total: number;
  outcome_summary: Record<string, number>;
}

export interface RunCallExchange {
  id: string;
  seq: number;
  method: string | null;
  url: string | null;
  request_headers: Record<string, string> | null;
  request_body: string | null;
  response_status: number | null;
  response_headers: Record<string, string> | null;
  response_body: string | null;
  error: string | null;
  latency_ms: number | null;
  created_at: string;
}

export interface PlatformStats {
  platform: Platform;
  model_used: string;
  total_responses: number;
  /** Effective (hollow-excluded) citations. */
  cited_count: number;
  citation_rate: number;
  hollow_count: number;
  prominence_breakdown: Record<string, number>;
  citation_type_breakdown: Record<string, number>;
}

export interface CompetitorStats {
  brand: string;
  cited_count: number;
  share_of_voice: number;
}

export interface CitationQuality {
  recommended: number;
  mentioned: number;
  negative: number;
  hollow: number;
  effective_total: number;
  recommended_pct: number;
  mentioned_pct: number;
  negative_pct: number;
}

export interface RunSummaryResponse {
  run: RunRead;
  total_analyses: number;
  /** Excludes hollow citations. */
  overall_citation_rate: number;
  /** Optional: absent when the API predates citation classification. */
  hollow_citation_count?: number;
  citation_quality?: CitationQuality;
  platform_stats: PlatformStats[];
  competitor_stats: CompetitorStats[];
  platform_errors: Record<string, string>;
}

export interface PromptAnalysisItem {
  platform: Platform;
  response_id: string;
  raw_response: string;
  model_used: string;
  latency_ms: number | null;
  cost_usd: number | null;
  client_cited: boolean | null;
  client_prominence: Prominence | null;
  client_sentiment: Sentiment | null;
  citation_type: CitationType | null;
  client_characterization: string | null;
  competitors_cited: Array<{ brand: string; prominence: string; sentiment: string }>;
  content_gaps: string[];
  citation_opportunity: CitationOpportunity | null;
  // The 1.0-5.0 score the bucket is derived from. Null before migration 0030.
  opportunity_score: number | null;
  reasoning: string | null;
}

export interface PromptDetail {
  prompt_id: string;
  prompt_text: string;
  category: string;
  results: PromptAnalysisItem[];
}

// ── Recommendations ───────────────────────────────────────────────────────────

export type RecommendationType =
  | "content_brief"
  | "schema_markup"
  | "llms_txt"
  | "authority_building";
export type RecommendationStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "revision_requested"
  | "implemented";
export type RecommendationPriority = "high" | "medium" | "low";

export interface RecommendationListItem {
  id: string;
  client_id: string;
  run_id: string | null;
  analysis_id: string | null;
  prompt_id: string | null;
  type: RecommendationType;
  status: RecommendationStatus;
  priority: RecommendationPriority;
  title: string;
  platform: string | null;
  target_query: string | null;
  reviewer_notes: string | null;
  generation_model: string | null;
  generation_cost_usd: number | null;
  created_at: string;
  updated_at: string;
  prompt_text: string | null;
  run_created_at: string | null;
  run_display_id?: string | null;
}

export interface RecommendationHistoryItem {
  id: string;
  old_status: string | null;
  new_status: string;
  actor: string;
  notes: string | null;
  created_at: string;
}

export interface RecommendationDetail extends RecommendationListItem {
  content: Record<string, unknown>;
  trigger_data: Record<string, unknown> | null;
  reviewer_id: string | null;
  reviewed_at: string | null;
  prompt_text: string | null;
  raw_response: string | null;
  analysis_data: {
    client_cited: boolean;
    client_prominence: Prominence;
    client_sentiment: Sentiment;
    client_characterization: string | null;
    competitors_cited: Array<{ brand: string; prominence: string; sentiment: string }>;
    content_gaps: string[];
    citation_opportunity: CitationOpportunity;
    reasoning: string;
  } | null;
  client_name: string | null;
  run_created_at: string | null;
  history: RecommendationHistoryItem[];
}

export interface RecommendationListResponse {
  items: RecommendationListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface RecommendationSummary {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  by_priority: Record<string, number>;
  last_generated_at: string | null;
  pending_high_priority: number;
  total_generation_cost_usd: number;
}

// One row of the per-run / per-prompt rollup (client Recommendations tab).
// key=null groups recs without a linked run/prompt (run deleted, or run-level
// types like llms.txt / authority building).
export interface RecommendationGroupItem {
  key: string | null;
  label: string | null; // run display_id / prompt text
  sublabel: string | null; // prompt category
  group_created_at: string | null; // run created_at
  total: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  last_rec_at: string | null;
}

export interface RecommendationGroupsResponse {
  group_by: "run" | "prompt";
  groups: RecommendationGroupItem[];
  total: number;
}
