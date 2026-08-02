import { useState, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ReplayRoundedIcon from "@mui/icons-material/ReplayRounded";
import FileDownloadRoundedIcon from "@mui/icons-material/FileDownloadRounded";
import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { runsApi, costApi, recommendationsApi, clientsApi } from "../../api/client";
import type { PromptDetail, RecommendationListItem, RunCallItem, RunCostSummary } from "../../types";
import { HBars } from "../ui/charts";
import {
  BarMeter, Chip, Drawer, EmptyState, RunStatusChip, fmtMs, pctFmt, platMeta, relTime, usdFmt, useConfirm, useToast,
} from "../ui/ui";
import { RecCard } from "../recommendations/RecCard";

const ACTIVE = new Set(["pending", "running"]);
// Terminal statuses that carry viewable results (partial = finished with drops).
const HAS_RESULTS = new Set(["completed", "partial"]);
// Statuses whose collected AI responses can be listed.
const SHOW_RESPONSES = new Set(["responses_ready", "completed", "partial"]);
// Statuses under which the run call log is worth showing (anything that ran).
const SHOW_DIAGNOSTICS = new Set(["running", "responses_ready", "completed", "partial", "failed", "cancelled"]);

// Typed outcome -> chip tone + label for the Diagnostics table.
function outcomeMeta(outcome: string): { label: string; tone: "" | "good" | "warn" | "bad" } {
  if (outcome === "success") return { label: "Success", tone: "good" };
  if (outcome === "timeout") return { label: "Timeout", tone: "warn" };
  if (outcome === "parse_error") return { label: "Bad JSON", tone: "bad" };
  if (outcome === "validation_error") return { label: "Bad shape", tone: "bad" };
  if (outcome === "http_error") return { label: "API error", tone: "bad" };
  if (outcome === "persist_error") return { label: "DB write failed", tone: "bad" };
  if (outcome === "cancelled") return { label: "Cancelled", tone: "" };
  return { label: outcome.replace(/_/g, " "), tone: "" };
}

const PHASE_LABEL: Record<string, string> = {
  monitoring: "Response collection",
  analysis: "Analysis",
  generation: "Recommendations",
};

// What record the attempt was about, for the Diagnostics table.
function callRecordLabel(call: RunCallItem): string {
  const prompt = call.prompt_text
    ? (call.prompt_text.length > 60 ? call.prompt_text.slice(0, 57) + "..." : call.prompt_text)
    : null;
  if (call.phase === "monitoring") return prompt ?? "-";
  if (call.phase === "analysis") {
    const engine = call.response_platform ? ` (${platMeta(call.response_platform).label} response)` : "";
    return (prompt ?? "response") + engine;
  }
  const type = call.rec_type ? call.rec_type.replace(/_/g, " ") : "recommendation";
  return prompt ? `${type}: ${prompt}` : type;
}

const PRIORITY_WEIGHT: Record<string, number> = { high: 0, medium: 1, low: 2 };

function fmtTokens(t: number | null | undefined): string {
  if (t == null) return "-";
  if (t >= 1_000_000) return `${(t / 1_000_000).toFixed(1)}M`;
  if (t >= 1_000) return `${Math.round(t / 1_000)}k`;
  return String(t);
}

// Input/output token columns: rows written before the split was persisted
// report 0 against a nonzero total, which is "unknown", not "none".
function fmtSplitTokens(part: number | null | undefined, total: number | null | undefined): string {
  if (part == null) return "-";
  if (part === 0 && (total ?? 0) > 0) return "-";
  return fmtTokens(part);
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// Actionable guidance for common platform failures.
const ERROR_HINTS: Array<[string, string]> = [
  ["credit balance is too low", "Add credits in the provider console under Plans and Billing"],
  ["quota", "API quota exceeded, check plan limits"],
  ["rate limit", "Too many concurrent requests, lower the per-platform concurrency"],
  ["invalid api key", "The API key is invalid, check the environment configuration"],
];

function errorHint(message: string): string | null {
  const lower = message.toLowerCase();
  for (const [fragment, guidance] of ERROR_HINTS) {
    if (lower.includes(fragment)) return guidance;
  }
  return null;
}

// One status per response: quality label when cited, blank when absent.
function citationTag(item?: { client_cited?: boolean | null; citation_type?: string | null }):
  { label: string; tone: "" | "good" | "warn" | "bad" } | null {
  if (!item || item.client_cited == null || !item.client_cited) return null;
  if (item.citation_type === "recommended") return { label: "Recommended", tone: "good" };
  if (item.citation_type === "negative") return { label: "Negative", tone: "bad" };
  if (item.citation_type === "hollow") return { label: "Hollow", tone: "warn" };
  return { label: "Cited", tone: "" };
}

// Citation opportunity: the 1.0-5.0 score, with the bucket for context.
// Analyses from before scoring existed show the bucket alone.
function opportunityLabel(item: { citation_opportunity?: string | null; opportunity_score?: number | null }): string | null {
  if (item.opportunity_score != null) {
    const bucket = item.citation_opportunity ? ` (${item.citation_opportunity})` : "";
    return `Opportunity: ${item.opportunity_score.toFixed(1)}/5.0${bucket}`;
  }
  return item.citation_opportunity ? `Opportunity: ${item.citation_opportunity}` : null;
}

// ── Cost by phase table ───────────────────────────────────────────────────────

function PhaseTable({ cost, durationLabel }: { cost: RunCostSummary; durationLabel: string }) {
  const rows: Array<[string, RunCostSummary["breakdown"]["monitoring"]]> = [
    ["Response collection", cost.breakdown?.monitoring ?? null],
    ["Analysis", cost.breakdown?.analysis ?? null],
    ["Recommendations", cost.breakdown?.generation ?? null],
  ];
  return (
    <table className="tb">
      <thead>
        <tr>
          <th>Phase</th>
          <th className="right">Time</th>
          <th className="right">Tokens in</th>
          <th className="right">Tokens out</th>
          <th className="right">Tokens</th>
          <th className="right">Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, phase]) => (
          <tr key={label}>
            <td>{label}</td>
            <td className="right mono">{phase ? fmtMs(phase.duration_ms) : "-"}</td>
            <td className="right mono">{phase ? fmtSplitTokens(phase.input_tokens, phase.tokens) : "-"}</td>
            <td className="right mono">{phase ? fmtSplitTokens(phase.output_tokens, phase.tokens) : "-"}</td>
            <td className="right mono">{phase ? fmtTokens(phase.tokens) : "-"}</td>
            <td className="right mono">{phase ? usdFmt(phase.cost_usd) : "-"}</td>
          </tr>
        ))}
        <tr>
          <td><b>Total</b></td>
          <td className="right mono"><b>{durationLabel}</b></td>
          <td className="right mono"><b>{fmtSplitTokens(cost.total_input_tokens, cost.total_tokens)}</b></td>
          <td className="right mono"><b>{fmtSplitTokens(cost.total_output_tokens, cost.total_tokens)}</b></td>
          <td className="right mono"><b>{fmtTokens(cost.total_tokens)}</b></td>
          <td className="right mono"><b>{usdFmt(cost.total_cost_usd)}</b></td>
        </tr>
      </tbody>
    </table>
  );
}

// ── Per-prompt response drawer ────────────────────────────────────────────────

function PromptResponsesDrawer({ detail, onClose }: { detail: PromptDetail; onClose: () => void }) {
  return (
    <Drawer onClose={onClose}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {detail.category && <span className="tag">{detail.category}</span>}
        <button
          style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0 }}
          onClick={onClose}
          aria-label="Close"
        >
          <CloseRoundedIcon style={{ fontSize: 17 }} />
        </button>
      </div>
      <h2>{detail.prompt_text}</h2>
      {detail.results.map((item) => {
        const p = platMeta(item.platform);
        const tag = citationTag(item);
        return (
          <div key={item.response_id} className="dsec">
            <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 9 }}>
              <span className="pd" style={{ width: 7, height: 7, borderRadius: 99, background: p.c, display: "inline-block" }} />
              <b style={{ fontSize: 12.5 }}>{p.label}</b>
              <span className="mono dim" style={{ fontSize: 10.5 }}>{item.model_used}</span>
              <div style={{ flex: 1 }} />
              {item.client_cited != null && (tag ? <Chip tone={tag.tone}>{tag.label}</Chip> : <Chip>Not cited</Chip>)}
            </div>
            <div className="dl">Response</div>
            <p style={{ marginBottom: 10 }}>{item.raw_response}</p>
            {item.client_cited != null && (
              <>
                <div className="dl">Analysis</div>
                <p>
                  {[
                    item.client_cited ? "Cited" : "Not cited",
                    item.citation_type && item.client_cited ? item.citation_type : null,
                    item.client_prominence && item.client_prominence !== "not_cited" ? `Prominence: ${item.client_prominence}` : null,
                    item.client_sentiment && item.client_sentiment !== "not_cited" ? `Sentiment: ${item.client_sentiment}` : null,
                    opportunityLabel(item),
                  ].filter(Boolean).join(". ")}
                  {item.competitors_cited.length > 0 && (
                    <> Competitors cited: {item.competitors_cited.map((c) => c.brand).join(", ")}.</>
                  )}
                </p>
                {item.reasoning && <p className="dim2" style={{ marginTop: 6 }}>{item.reasoning}</p>}
              </>
            )}
          </div>
        );
      })}
    </Drawer>
  );
}

// ── Call diagnostics drawer ───────────────────────────────────────────────────

function headerLines(headers: Record<string, string> | null): string {
  if (!headers) return "-";
  const entries = Object.entries(headers);
  return entries.length ? entries.map(([k, v]) => `${k}: ${v}`).join("\n") : "-";
}

function CallDrawer({
  clientId, runId, call, onClose,
}: { clientId: string; runId: string; call: RunCallItem; onClose: () => void }) {
  const meta = outcomeMeta(call.outcome);
  const { data: exchanges } = useQuery({
    queryKey: ["admin-run-call-exchanges", runId, call.id],
    queryFn: () => runsApi.getCallExchanges(clientId, runId, call.id),
    enabled: call.has_exchanges,
  });

  const facts: Array<[string, string]> = [
    ["Phase", PHASE_LABEL[call.phase] ?? call.phase],
    ["Attempt", String(call.attempt)],
    ["Provider called", [call.platform, call.model].filter(Boolean).join(", ") || "-"],
    ["HTTP status", call.http_status != null ? String(call.http_status) : "-"],
    ["Latency", call.latency_ms != null ? fmtMs(call.latency_ms) : "-"],
    ["Tokens", call.tokens_used != null ? String(call.tokens_used) : "-"],
    ["Tokens in", call.input_tokens != null ? String(call.input_tokens) : "-"],
    ["Tokens out", call.output_tokens != null ? String(call.output_tokens) : "-"],
    ["Cost", call.cost_usd != null ? usdFmt(call.cost_usd) : "-"],
    ["At", new Date(call.created_at).toLocaleString()],
  ];

  const pre: CSSProperties = {
    whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11,
    background: "var(--bg2, rgba(127,127,127,0.08))", borderRadius: 8, padding: "8px 10px",
    maxHeight: 260, overflow: "auto", marginTop: 4,
  };

  return (
    <Drawer onClose={onClose}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Chip tone={meta.tone}>{meta.label}</Chip>
        {call.error_type && <span className="mono dim" style={{ fontSize: 11 }}>{call.error_type}</span>}
        <button
          style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0 }}
          onClick={onClose}
          aria-label="Close"
        >
          <CloseRoundedIcon style={{ fontSize: 17 }} />
        </button>
      </div>
      <h2 style={{ fontSize: 15 }}>{callRecordLabel(call)}</h2>

      <div className="dsec">
        <div className="dl">Attempt</div>
        <table className="tb">
          <tbody>
            {facts.map(([label, value]) => (
              <tr key={label}>
                <td style={{ width: 130, color: "var(--ink4)" }}>{label}</td>
                <td className="mono" style={{ fontSize: 12 }}>{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {call.error_detail && (
        <div className="dsec">
          <div className="dl">Error</div>
          <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{call.error_detail}</p>
        </div>
      )}

      {call.has_exchanges && (
        <div className="dsec">
          <div className="dl">HTTP traffic (credentials redacted)</div>
          {(exchanges ?? []).map((ex) => (
            <div key={ex.id} style={{ marginBottom: 14 }}>
              <div className="mono" style={{ fontSize: 11.5, marginBottom: 4 }}>
                {[ex.method, ex.url].filter(Boolean).join(" ") || "captured exchange"}
                {ex.response_status != null && <> {"->"} {ex.response_status}</>}
                {ex.latency_ms != null && <span className="dim"> ({fmtMs(ex.latency_ms)})</span>}
              </div>
              {ex.error && <p className="dim2" style={{ fontSize: 11.5 }}>{ex.error}</p>}
              {ex.request_headers && Object.keys(ex.request_headers).length > 0 && (
                <>
                  <div className="dl" style={{ marginTop: 6 }}>Request headers</div>
                  <pre style={pre}>{headerLines(ex.request_headers)}</pre>
                </>
              )}
              {ex.request_body && (
                <>
                  <div className="dl" style={{ marginTop: 6 }}>Request body</div>
                  <pre style={pre}>{ex.request_body}</pre>
                </>
              )}
              {ex.response_headers && Object.keys(ex.response_headers).length > 0 && (
                <>
                  <div className="dl" style={{ marginTop: 6 }}>Response headers</div>
                  <pre style={pre}>{headerLines(ex.response_headers)}</pre>
                </>
              )}
              {ex.response_body && (
                <>
                  <div className="dl" style={{ marginTop: 6 }}>Response body</div>
                  <pre style={pre}>{ex.response_body}</pre>
                </>
              )}
            </div>
          ))}
          {exchanges != null && exchanges.length === 0 && (
            <p className="dim2">No exchanges stored for this attempt.</p>
          )}
        </div>
      )}
    </Drawer>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function RunDetail() {
  const { clientId, runId } = useParams<{ clientId: string; runId: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const [promptFilter, setPromptFilter] = useState<"all" | "cited">("all");
  const [selectedPrompt, setSelectedPrompt] = useState<PromptDetail | null>(null);
  const [selectedCall, setSelectedCall] = useState<RunCallItem | null>(null);
  const [downloading, setDownloading] = useState<"json" | "pdf" | "log" | null>(null);

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const { data: summary } = useQuery({
    queryKey: ["admin-run-detail", clientId, runId],
    queryFn: () => runsApi.get(clientId!, runId!),
    enabled: !!clientId && !!runId,
    // Poll while the run itself is active, or while a staged generation runs.
    refetchInterval: (q) => {
      const r = q.state.data?.run;
      return ACTIVE.has(r?.status ?? "") || r?.generation_status === "running" ? 2000 : false;
    },
  });

  const { data: prompts } = useQuery({
    queryKey: ["admin-run-prompts", clientId, runId],
    queryFn: () => runsApi.getPrompts(clientId!, runId!),
    enabled: SHOW_RESPONSES.has(summary?.run?.status ?? ""),
  });

  // Live spend (R5): fetched during the run too, ticking while it's active.
  const { data: cost } = useQuery<RunCostSummary>({
    queryKey: ["admin-run-costs", clientId, runId],
    queryFn: () => costApi.getRunCosts(clientId!, runId!),
    enabled: !!summary?.run,
    refetchInterval: () => (ACTIVE.has(summary?.run?.status ?? "") ? 5000 : false),
  });

  const { data: runRecs } = useQuery({
    queryKey: ["client-recs", clientId, "run", runId],
    queryFn: () => recommendationsApi.list(clientId!, { run_id: runId, per_page: 100 }),
    enabled: !!clientId && !!runId && HAS_RESULTS.has(summary?.run?.status ?? ""),
  });

  // Run call log: every failed upstream attempt with its typed reason
  // (timeout vs bad JSON vs API error) — live during the run, final after.
  const { data: callLog } = useQuery({
    queryKey: ["admin-run-calls", clientId, runId],
    queryFn: () => runsApi.getCalls(clientId!, runId!, "failed"),
    enabled: !!clientId && !!runId && SHOW_DIAGNOSTICS.has(summary?.run?.status ?? ""),
    refetchInterval: () => (ACTIVE.has(summary?.run?.status ?? "") ? 5000 : false),
  });

  const qc = useQueryClient();
  const invalidateRun = () => {
    qc.invalidateQueries({ queryKey: ["admin-run-detail", clientId, runId] });
    qc.invalidateQueries({ queryKey: ["admin-runs", clientId] });
  };
  // Kill switch (R4): stop the run — no new API spend after confirmation.
  const cancelMut = useMutation({
    mutationFn: () => runsApi.cancel(clientId!, runId!),
    onSuccess: () => { invalidateRun(); toast("Run cancelled"); },
  });
  // Staged runs: advance a parked run into analysis / generate recommendations.
  const analyzeMut = useMutation({
    mutationFn: () => runsApi.analyze(clientId!, runId!),
    onSuccess: () => { invalidateRun(); toast("Analysis started"); },
  });
  const generateMut = useMutation({
    mutationFn: () => runsApi.generate(clientId!, runId!),
    onSuccess: () => { invalidateRun(); toast("Recommendation generation started"); },
  });

  async function handleDownload(format: "json" | "pdf" | "log") {
    if (!clientId || !runId) return;
    setDownloading(format);
    try {
      if (format === "log") {
        const blob = await runsApi.downloadLog(clientId, runId);
        triggerDownload(blob, `${displayId}-call-log.ndjson`);
        toast("call log downloaded");
      } else {
        const blob = format === "json"
          ? await runsApi.downloadJson(clientId, runId)
          : await runsApi.downloadPdf(clientId, runId);
        triggerDownload(blob, `${displayId}-report.${format}`);
        toast(`report.${format} downloaded`);
      }
    } finally {
      setDownloading(null);
    }
  }

  const run = summary?.run;
  const displayId = (run as { display_id?: string } | undefined)?.display_id ?? (runId?.slice(0, 8) ?? "run");
  const hasResults = HAS_RESULTS.has(run?.status ?? "");
  const perr = summary?.platform_errors ?? {};
  const perrCount = Object.keys(perr).length;

  // Collection and analysis run concurrently, so their durations must not be
  // summed — the run records their combined elapsed time for exactly this.
  const workedMsTotal = (() => {
    const b = cost?.breakdown;
    const timings = summary?.run?.phase_timings;
    const collectAnalyze =
      timings?.collection_analysis_ms ??
      (b?.monitoring?.duration_ms ?? 0) + (b?.analysis?.duration_ms ?? 0);
    const sum = collectAnalyze + (b?.generation?.duration_ms ?? 0);
    return sum > 0 ? sum : null;
  })();

  const quality = summary?.citation_quality;
  const filteredPrompts = (prompts ?? []).filter((p) =>
    promptFilter === "cited" ? p.results.some((r) => r.client_cited) : true
  );

  const recs = [...(runRecs?.items ?? [])].sort(
    (a, b) => (PRIORITY_WEIGHT[a.priority] ?? 3) - (PRIORITY_WEIGHT[b.priority] ?? 3)
  );
  const openRec = (rec: RecommendationListItem) =>
    navigate(`/clients/${clientId}/recommendations?rec=${rec.id}`);

  const clientNameLower = (client?.name ?? "").toLowerCase();

  return (
    <>
      <div className="phead">
        <div className="grow">
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1 className="page mono" style={{ fontSize: 17 }}>{displayId}</h1>
            {run && <RunStatusChip status={run.status} />}
          </div>
          <div className="sub">
            {run && new Date(run.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            {client && <>, {client.name}</>}
          </div>
        </div>
        {hasResults && (
          <>
            <button className="btn" disabled={!!downloading} onClick={() => handleDownload("json")}>
              <FileDownloadRoundedIcon style={{ fontSize: 14 }} /> JSON
            </button>
            <button className="btn" disabled={!!downloading} onClick={() => handleDownload("pdf")}>
              <FileDownloadRoundedIcon style={{ fontSize: 14 }} /> PDF
            </button>
          </>
        )}
        {run && SHOW_DIAGNOSTICS.has(run.status) && !ACTIVE.has(run.status) && (
          <button className="btn" disabled={!!downloading} onClick={() => handleDownload("log")}>
            <FileDownloadRoundedIcon style={{ fontSize: 14 }} /> Log
          </button>
        )}
      </div>

      {/* Active run */}
      {run && ACTIVE.has(run.status) && (
        <div className="banner live">
          <span className="bi" style={{ color: "var(--white)" }}><PlayArrowRoundedIcon style={{ fontSize: 16 }} /></span>
          <div style={{ flex: 1 }}>
            <b>Run in progress</b>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 9 }}>
              <BarMeter pct={run.total_prompts > 0 ? (run.completed_prompts / run.total_prompts) * 100 : 0} width={420} />
              <span className="mono" style={{ fontSize: 11.5 }}>{run.completed_prompts}/{run.total_prompts}</span>
              {cost?.total_cost_usd != null && (
                <span className="mono dim" style={{ fontSize: 11.5 }}>live spend {usdFmt(cost.total_cost_usd)}</span>
              )}
            </div>
          </div>
          <button
            className="btn sm danger"
            disabled={cancelMut.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "Cancel this run?",
                message: "No new API calls will be made; work done so far is kept.",
                confirmLabel: "Cancel run",
                cancelLabel: "Keep running",
                danger: true,
              });
              if (ok) cancelMut.mutate();
            }}
          >
            <CloseRoundedIcon style={{ fontSize: 13 }} /> Cancel run
          </button>
        </div>
      )}

      {/* Staged run parked: responses collected, analysis awaits a click */}
      {run?.status === "responses_ready" && (
        <div className="banner warn">
          <span className="bi"><WarningAmberRoundedIcon style={{ fontSize: 16 }} /></span>
          <div style={{ flex: 1 }}>
            <b>Responses collected, awaiting analysis</b>
            <div className="note">
              {run.completed_prompts}/{run.total_prompts} responses stored
              {cost?.total_cost_usd != null && <>, collection cost {usdFmt(cost.total_cost_usd)}</>}.
              Analysis and recommendations run only when you start them.
            </div>
          </div>
          <button className="btn pri sm" disabled={analyzeMut.isPending} onClick={() => analyzeMut.mutate()}>
            <PlayArrowRoundedIcon style={{ fontSize: 13 }} /> Start analysis
          </button>
          <button
            className="btn sm danger"
            disabled={cancelMut.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "Discard this run?",
                message: "Its collected responses will never be analyzed. No new API calls will be made.",
                confirmLabel: "Discard run",
                danger: true,
              });
              if (ok) cancelMut.mutate();
            }}
          >
            Discard
          </button>
        </div>
      )}

      {/* Staged/retry generation: results exist, recommendations don't yet */}
      {run && hasResults && (run.generation_status === "pending" || run.generation_status === "failed") && (
        <div className="banner">
          <span className="bi dim"><InfoOutlinedIcon style={{ fontSize: 15 }} /></span>
          <div style={{ flex: 1 }}>
            <b>{run.generation_status === "failed" ? "Recommendation generation failed" : "Recommendations not generated yet"}</b>
            <div className="note">Analysis results are final; generating recommendations adds LLM spend but never changes them.</div>
          </div>
          <button className="btn pri sm" disabled={generateMut.isPending} onClick={() => generateMut.mutate()}>
            {run.generation_status === "failed"
              ? <><ReplayRoundedIcon style={{ fontSize: 13 }} /> Retry generation</>
              : <><PlayArrowRoundedIcon style={{ fontSize: 13 }} /> Generate recommendations</>}
          </button>
        </div>
      )}

      {run && hasResults && run.generation_status === "running" && (
        <div className="banner live">
          <span className="bi" style={{ color: "var(--white)" }}><PlayArrowRoundedIcon style={{ fontSize: 16 }} /></span>
          <div><b>Generating recommendations...</b></div>
        </div>
      )}

      {/* Cancelled run */}
      {run?.status === "cancelled" && (
        <div className="banner">
          <span className="bi dim"><InfoOutlinedIcon style={{ fontSize: 15 }} /></span>
          <div>
            <b>Run cancelled</b>
            <div className="note">
              {run.completed_prompts}/{run.total_prompts} prompts collected before stop
              {cost?.total_cost_usd != null && <>, {usdFmt(cost.total_cost_usd)} spent</>}. No new API calls were made after cancellation.
            </div>
          </div>
        </div>
      )}

      {/* Platform errors */}
      {perrCount > 0 && (
        <div className="banner warn">
          <span className="bi"><WarningAmberRoundedIcon style={{ fontSize: 16 }} /></span>
          <div>
            <b>{perrCount} issue{perrCount > 1 ? "s" : ""}, results are partial</b>
            {Object.entries(perr).map(([p, msg]) => {
              const hint = errorHint(msg);
              return (
                <div key={p} className="note">
                  <span className="mono">{p}</span>: {msg}{hint ? ` (${hint})` : ""}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {hasResults && summary && (
        <>
          <div className="cards">
            <div className="card">
              <div className="lbl">Citation rate</div>
              <div className="val">{pctFmt(summary.overall_citation_rate)}</div>
              <div className="hint">hollow excluded, {summary.total_analyses} responses</div>
            </div>
            <div className="card">
              <div className="lbl">Recommended</div>
              <div className="val">{Math.round((quality?.recommended_pct ?? 0) * 100)}%</div>
              <div className="hint">of real citations</div>
            </div>
            <div className="card">
              <div className="lbl">Negative</div>
              <div className="val">{Math.round((quality?.negative_pct ?? 0) * 100)}%</div>
              <div className="hint">{quality?.negative ?? 0} flagged</div>
            </div>
            <div className="card">
              <div className="lbl">Run cost</div>
              <div className="val" style={{ fontSize: 28 }}>{usdFmt(cost?.total_cost_usd)}</div>
              <div className="hint">{workedMsTotal != null ? `${fmtMs(workedMsTotal)} working time` : "working time unavailable"}</div>
            </div>
          </div>

          <div className="grid2">
            <div className="panel">
              <div className="ph">
                <h3>Citation by prompt</h3>
                <span className="note">first {Math.min((prompts ?? []).length, 8)}</span>
              </div>
              {(prompts ?? []).length > 0 ? (
                <HBars
                  max={1}
                  rows={(prompts ?? []).slice(0, 8).map((p) => {
                    const cited = p.results.filter((r) => r.client_cited).length;
                    return {
                      label: p.prompt_text.length > 46 ? p.prompt_text.slice(0, 46) + "..." : p.prompt_text,
                      v: p.results.length > 0 ? cited / p.results.length : 0,
                      right: `${cited}/${p.results.length}`,
                    };
                  })}
                />
              ) : (
                <EmptyState>No prompt data.</EmptyState>
              )}
            </div>

            <div className="panel">
              <div className="ph">
                <h3>By platform</h3>
                <span className="note">cited / prompts, model</span>
              </div>
              {(summary.platform_stats ?? []).map((ps) => {
                const p = platMeta(ps.platform);
                const failed = !!perr[ps.platform];
                return (
                  <div
                    key={ps.platform}
                    style={{ display: "flex", alignItems: "center", gap: 12, border: "1px solid var(--bf)", borderRadius: 10, padding: "11px 14px", marginBottom: 9 }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: p.c, flexShrink: 0 }} />
                    <div>
                      <b style={{ fontSize: 13 }}>{p.label}</b>
                      <div className="mono dim" style={{ fontSize: 10.5 }}>{ps.model_used}</div>
                    </div>
                    <div style={{ flex: 1 }} />
                    {failed
                      ? <Chip tone="bad">failed</Chip>
                      : <span className="mono" style={{ fontSize: 13 }}>{ps.cited_count}/{ps.total_responses}</span>}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="grid2">
            <div className="panel">
              <div className="ph"><h3>Competitor share of voice</h3></div>
              {(summary.competitor_stats ?? []).length > 0 ? (
                <HBars
                  rows={summary.competitor_stats.slice(0, 6).map((c) => ({
                    label: c.brand,
                    v: c.share_of_voice,
                    right: `${pctFmt(c.share_of_voice)}, ${c.cited_count}`,
                    self: c.brand.toLowerCase() === clientNameLower,
                  }))}
                />
              ) : (
                <EmptyState>No competitor citations in this run.</EmptyState>
              )}
            </div>

            <div className="panel">
              <div className="ph"><h3>Cost and usage by phase</h3></div>
              {cost ? (
                <PhaseTable cost={cost} durationLabel={workedMsTotal != null ? fmtMs(workedMsTotal) : "-"} />
              ) : (
                <EmptyState>Cost data unavailable.</EmptyState>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="ph">
              <h3>Recommendations from this run</h3>
              <span className="note">{recs.length} generated, impact-ranked</span>
              <div className="sp" />
              <Link className="btn sm" to={`/clients/${clientId}/recommendations`}>
                Full queue <ArrowForwardRoundedIcon style={{ fontSize: 13 }} />
              </Link>
            </div>
            {recs.length > 0 ? (
              recs.map((rec) => <RecCard key={rec.id} rec={rec} onOpen={openRec} clientPath={clientId} />)
            ) : (
              <EmptyState>
                {run?.generation_status === "pending"
                  ? "Staged run, generate after analysis."
                  : "The engine generated no recommendations for this run."}
              </EmptyState>
            )}
          </div>
        </>
      )}

      {/* Collected responses / prompt drill-down */}
      {SHOW_RESPONSES.has(run?.status ?? "") && prompts && prompts.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)" }}>
            <div>
              <h3 style={{ fontSize: 13.5, fontWeight: 650 }}>
                {run?.status === "responses_ready" ? "Collected responses" : "Prompt drill-down"}
              </h3>
              <span style={{ color: "var(--ink4)", fontSize: 11.5 }}>
                {run?.status === "responses_ready"
                  ? "Analysis not run yet; citation columns fill in after analysis"
                  : "Open a prompt for per-platform output"}
              </span>
            </div>
            <div style={{ flex: 1 }} />
            <div className="pillrow">
              {(["all", "cited"] as const).map((f) => (
                <button key={f} className={`pi${promptFilter === f ? " on" : ""}`} onClick={() => setPromptFilter(f)}>
                  {f === "all" ? `All (${prompts.length})` : "Cited"}
                </button>
              ))}
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th style={{ width: "44%" }}>Prompt</th>
                  {["anthropic", "gemini", "openai", "perplexity"].map((p) => (
                    <th key={p} style={{ textAlign: "center" }}>{platMeta(p).label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredPrompts.map((p) => (
                  <tr key={p.prompt_id} className="rowlink" onClick={() => setSelectedPrompt(p)}>
                    <td style={{ fontSize: 13 }}>{p.prompt_text}</td>
                    {["anthropic", "gemini", "openai", "perplexity"].map((platform) => {
                      const result = p.results.find((r) => r.platform === platform);
                      const tag = result ? citationTag(result) : null;
                      return (
                        <td key={platform} style={{ textAlign: "center" }}>
                          {tag ? <Chip tone={tag.tone}>{tag.label}</Chip> : <span className="dim">-</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {filteredPrompts.length === 0 && (
                  <tr><td colSpan={5}><EmptyState>No prompts match this filter.</EmptyState></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Diagnostics: every dropped call with its exact typed reason */}
      {callLog && callLog.items.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)", flexWrap: "wrap" }}>
            <div>
              <h3 style={{ fontSize: 13.5, fontWeight: 650 }}>Diagnostics</h3>
              <span style={{ color: "var(--ink4)", fontSize: 11.5 }}>
                {callLog.items.length} failed call attempt{callLog.items.length > 1 ? "s" : ""}, open a row for the full request and response
              </span>
            </div>
            <div style={{ flex: 1 }} />
            {Object.entries(callLog.outcome_summary)
              .filter(([outcome]) => outcome !== "success")
              .map(([outcome, count]) => {
                const meta = outcomeMeta(outcome);
                return <Chip key={outcome} tone={meta.tone}>{meta.label}: {count}</Chip>;
              })}
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th>Phase</th>
                  <th style={{ width: "34%" }}>Record</th>
                  <th>Outcome</th>
                  <th>Reason</th>
                  <th className="right">HTTP</th>
                  <th className="right">Time</th>
                  <th className="right">Attempt</th>
                </tr>
              </thead>
              <tbody>
                {callLog.items.map((call) => {
                  const meta = outcomeMeta(call.outcome);
                  return (
                    <tr key={call.id} className="rowlink" onClick={() => setSelectedCall(call)}>
                      <td style={{ whiteSpace: "nowrap" }}>{PHASE_LABEL[call.phase] ?? call.phase}</td>
                      <td style={{ fontSize: 12.5 }}>{callRecordLabel(call)}</td>
                      <td><Chip tone={meta.tone}>{meta.label}</Chip></td>
                      <td className="dim" style={{ fontSize: 11.5, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {call.error_detail ?? "-"}
                      </td>
                      <td className="right mono">{call.http_status ?? "-"}</td>
                      <td className="right mono">{call.latency_ms != null ? fmtMs(call.latency_ms) : "-"}</td>
                      <td className="right mono">{call.attempt}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selectedPrompt && (
        <PromptResponsesDrawer detail={selectedPrompt} onClose={() => setSelectedPrompt(null)} />
      )}
      {selectedCall && clientId && runId && (
        <CallDrawer clientId={clientId} runId={runId} call={selectedCall} onClose={() => setSelectedCall(null)} />
      )}
    </>
  );
}
