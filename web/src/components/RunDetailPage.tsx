import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import FileDownloadRoundedIcon from "@mui/icons-material/FileDownloadRounded";
import { dashboard } from "../lib/api";
import type { RunCostSummary } from "../lib/api";
import { useAuth } from "../auth/AuthContext";
import { ByPlatformPanel } from "./SummaryCards";
import { PromptTable } from "./PromptTable";
import { PlatformErrorBanner } from "./PlatformErrorBanner";
import { RunProgress } from "./RunProgress";
import { EmptyState, RunStatusChip, pctFmt, relTime, usdFmt } from "./ui";

const ACTIVE = new Set(["pending", "running"]);
// Terminal statuses that carry viewable results (partial = finished with drops).
const HAS_RESULTS = new Set(["completed", "partial"]);

function fmtTokens(n: number | null | undefined) {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "-";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const QUALITY_META: { key: "recommended" | "mentioned" | "negative"; label: string; c: string }[] = [
  { key: "recommended", label: "Recommended", c: "var(--good)" },
  { key: "mentioned", label: "Neutral", c: "var(--ink5)" },
  { key: "negative", label: "Negative", c: "var(--bad)" },
];

function CostUsagePanel({ runId, showDuration }: { runId: string; showDuration: boolean }) {
  const { data: cost } = useQuery<RunCostSummary>({
    queryKey: ["run-costs", runId],
    queryFn: () => dashboard.getRunCosts(runId),
    enabled: !!runId,
  });

  if (!cost || cost.total_cost_usd == null) return null;

  const rows: Array<[string, RunCostSummary["breakdown"]["monitoring"]]> = [
    ["Response collection", cost.breakdown?.monitoring ?? null],
    ["Analysis", cost.breakdown?.analysis ?? null],
    ["Recommendations", cost.breakdown?.generation ?? null],
  ];
  const totalCalls = rows.reduce((s, [, p]) => s + (p?.api_calls ?? 0), 0);
  const totalMs = rows.reduce((s, [, p]) => s + (p?.duration_ms ?? 0), 0);

  return (
    <div className="panel">
      <div className="ph">
        <h3>Cost and usage</h3>
        <span className="note">by phase</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="tb">
          <thead>
            <tr>
              <th>Phase</th>
              <th className="right">API calls</th>
              <th className="right">Tokens</th>
              {showDuration && <th className="right">Time</th>}
              <th className="right">Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, p]) => (
              <tr key={label}>
                <td>{label}</td>
                <td className="right mono">{p ? p.api_calls : "-"}</td>
                <td className="right mono">{p ? fmtTokens(p.tokens) : "-"}</td>
                {showDuration && <td className="right mono">{p ? fmtMs(p.duration_ms) : "-"}</td>}
                <td className="right mono">{p ? usdFmt(p.cost_usd) : "-"}</td>
              </tr>
            ))}
            <tr>
              <td><b>Total</b></td>
              <td className="right mono"><b>{totalCalls}</b></td>
              <td className="right mono"><b>{fmtTokens(cost.total_tokens)}</b></td>
              {showDuration && <td className="right mono"><b>{totalMs > 0 ? fmtMs(totalMs) : "-"}</b></td>}
              <td className="right mono"><b>{usdFmt(cost.total_cost_usd)}</b></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function RunDetailPage() {
  const { display } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const [downloading, setDownloading] = useState<"json" | "pdf" | null>(null);

  const { data: runData, isLoading } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => dashboard.getRunDetail(runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const s = q.state.data?.run?.status;
      return s && ACTIVE.has(s) ? 2000 : false;
    },
  });

  const { data: prompts } = useQuery({
    queryKey: ["run-prompts", runId],
    queryFn: () => dashboard.getRunPrompts(runId!),
    enabled: HAS_RESULTS.has(runData?.run?.status ?? ""),
  });

  async function handleDownload(format: "json" | "pdf") {
    if (!runId) return;
    setDownloading(format);
    try {
      const blob = format === "json"
        ? await dashboard.downloadRunJson(runId)
        : await dashboard.downloadRunPdf(runId);
      const run = runData?.run;
      const base = (run as { display_id?: string } | undefined)?.display_id ?? runId.slice(0, 8);
      triggerDownload(blob, `${base}-report.${format}`);
    } finally {
      setDownloading(null);
    }
  }

  if (isLoading) return <EmptyState>Loading...</EmptyState>;
  if (!runData) return <EmptyState>Run not found.</EmptyState>;

  const run = runData.run;
  const displayId = (run as { display_id?: string }).display_id ?? run.id.slice(0, 8);
  const createdAt = run.created_at.endsWith("Z") ? run.created_at : run.created_at + "Z";
  const runTitle = display.run_ids
    ? displayId
    : `${new Date(createdAt).toLocaleDateString([], { month: "short", day: "numeric" })} run`;
  const quality = runData.citation_quality;
  const hasResults = HAS_RESULTS.has(run.status);

  return (
    <>
      <div className="phead">
        <div className="grow">
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <Link to="/dashboard/runs" className="dim" style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 3 }}>
              <ArrowBackRoundedIcon style={{ fontSize: 13 }} /> Run history
            </Link>
            <h1 className={`page${display.run_ids ? " mono" : ""}`} style={{ fontSize: 15 }}>{runTitle}</h1>
            {display.status && <RunStatusChip status={run.status} />}
          </div>
          <div className="sub">
            {relTime(run.created_at)}{display.progress && `, ${run.completed_prompts}/${run.total_prompts} prompts`}, 4 platforms
          </div>
        </div>
        {hasResults && (
          <>
            <button className="btn sm" disabled={!!downloading} onClick={() => handleDownload("json")}>
              <FileDownloadRoundedIcon style={{ fontSize: 13 }} /> JSON
            </button>
            <button className="btn sm" disabled={!!downloading} onClick={() => handleDownload("pdf")}>
              <FileDownloadRoundedIcon style={{ fontSize: 13 }} /> PDF
            </button>
          </>
        )}
      </div>

      {ACTIVE.has(run.status) && <RunProgress run={run} />}

      {display.status && Object.keys(runData.platform_errors ?? {}).length > 0 && (
        <PlatformErrorBanner errors={runData.platform_errors} />
      )}

      {hasResults && (
        <>
          {(() => {
            const citationPanel = (
              <div className="panel">
                <div className="ph"><h3>Citation rate</h3></div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                  <span className="mono" style={{ fontSize: 34 }}>{pctFmt(runData.overall_citation_rate)}</span>
                  <span className="dim" style={{ fontSize: 12 }}>across {runData.total_analyses} responses</span>
                </div>
                {quality && quality.effective_total > 0 && (
                  <>
                    <div className="qbar" style={{ marginTop: 14 }}>
                      {QUALITY_META.map(({ key, c }) => {
                        const w = Math.round(quality[`${key}_pct`] * 100);
                        if (w === 0) return null;
                        return <i key={key} style={{ width: `${w}%`, background: c }} />;
                      })}
                    </div>
                    {QUALITY_META.map(({ key, label, c }) => (
                      <div key={key} className="qrow">
                        <span className="d" style={{ background: c }} />
                        {label}
                        <span className="r">{Math.round(quality[`${key}_pct`] * 100)}%</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            );
            // Cost hidden (client default): citation rate stands alone, full width.
            return display.cost ? (
              <div className="grid2" style={{ gridTemplateColumns: "1fr 1.4fr" }}>
                {citationPanel}
                <CostUsagePanel runId={run.id} showDuration={display.duration} />
              </div>
            ) : (
              citationPanel
            );
          })()}

          {display.platforms && <ByPlatformPanel summary={runData} showModelIds={display.model_ids} />}

          {display.prompts && prompts && prompts.length > 0 && (
            <PromptTable prompts={prompts} showResponses={display.responses} showModelIds={display.model_ids} />
          )}
        </>
      )}
    </>
  );
}
