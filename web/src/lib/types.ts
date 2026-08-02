export type Platform = "perplexity" | "openai" | "anthropic" | "gemini";
// "partial": terminal with results, but some monitoring calls or analyses
// were dropped — never displayed as "completed".
// "cancelled": an admin pulled the kill switch on an in-flight run.
// "responses_ready": staged run parked after monitoring — responses are
// collected, analysis awaits an admin click (read-only here).
export type RunStatus =
  "pending" | "running" | "responses_ready" | "completed" | "partial" | "failed" | "cancelled";
export type Prominence = "primary" | "secondary" | "mentioned" | "not_cited";
export type Sentiment = "positive" | "neutral" | "negative" | "not_cited";
export type CitationOpportunity = "high" | "medium" | "low";
export type CitationType = "recommended" | "mentioned" | "negative" | "hollow" | "not_cited";

export interface ClientRead {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
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
  error_message?: string | null;
  created_at: string;
  updated_at: string;
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
  /** Fractions (0-1) of the effective citations. */
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
  /** Keyed by platform name; present when one or more platform API calls failed. */
  platform_errors: Record<string, string>;
}

export interface PromptAnalysisItem {
  platform: Platform;
  response_id: string;
  raw_response: string;
  model_used: string;
  latency_ms?: number | null;
  cost_usd?: number | null;
  // null while analysis is in progress
  client_cited?: boolean | null;
  client_prominence?: Prominence | null;
  client_sentiment?: Sentiment | null;
  citation_type?: CitationType | null;
  client_characterization?: string | null;
  competitors_cited: Array<{ brand: string; prominence: string; sentiment: string }>;
  content_gaps: string[];
  citation_opportunity?: CitationOpportunity | null;
  // The 1.0-5.0 score the bucket is derived from. Null before migration 0030.
  opportunity_score?: number | null;
  reasoning?: string | null;
}

export interface PromptDetail {
  prompt_id: string;
  prompt_text: string;
  category: string;
  results: PromptAnalysisItem[];
}

// ── Prompt management ─────────────────────────────────────────────────────────

// Categories are admin-managed (not a fixed enum); "" means no category.
export type PromptCategory = string;

export interface PromptRead {
  id: string;
  client_id: string;
  text: string;
  category: PromptCategory;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptCreate {
  text: string;
  category: PromptCategory;
}

export interface PromptUpdate {
  text?: string;
  category?: PromptCategory;
  is_active?: boolean;
}

export interface PromptListResponse {
  items: PromptRead[];
  total: number;
  page: number;
  per_page: number;
}

export interface PromptBulkCreate {
  prompts: PromptCreate[];
}

export interface PromptBulkResult {
  created: number;
  skipped: number;
  errors: string[];
}

export interface DashboardSummary {
  client_name: string;
  latest_run_id: string | null;
  latest_run_status: string | null;
  latest_run_date: string | null;
  latest_citation_rate: number | null;
  visibility_score: number | null;
  citation_quality: CitationQuality | null;
  hollow_citation_count: number;
  citation_rate_trend: Array<{ run_id: string; date: string; citation_rate: number }>;
  total_prompts: number;
  total_runs: number;
  schedule_enabled: boolean;
  schedule_cadence: string;
  next_scheduled_run_at: string | null;
}

export interface AuditLogRead {
  id: string;
  client_id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogRead[];
  total: number;
  page: number;
  per_page: number;
}

export interface PromptListFilters {
  category?: PromptCategory | "";
  is_active?: boolean;
  search?: string;
  page?: number;
  per_page?: number;
}
