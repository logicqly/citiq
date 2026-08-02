import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { dashboard } from "../lib/api";
import { useAuth } from "../auth/AuthContext";
import { RunProgress } from "./RunProgress";
import { SummaryCards } from "./SummaryCards";
import { PromptTable } from "./PromptTable";
import { PlatformErrorBanner } from "./PlatformErrorBanner";
import type { DashboardSummary, RunSummaryResponse } from "../lib/types";
import { AreaChart, Chip, EmptyState, OrigoMark, pctFmt, relTime } from "./ui";

const ACTIVE = new Set(["pending", "running"]);
// Terminal statuses that carry viewable results (partial = finished with drops).
const HAS_RESULTS = new Set(["completed", "partial"]);

function timeUntil(iso: string | null): string | null {
  if (!iso) return null;
  const diff = new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime() - Date.now();
  if (diff <= 0) return "now";
  const m = Math.floor(diff / 60000);
  if (m < 60) return `in ${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  if (h < 24) return rem > 0 ? `in ${h}h ${rem}m` : `in ${h}h`;
  return `in ${Math.floor(h / 24)}d`;
}

function nextRunLabel(summary: DashboardSummary | undefined): string | null {
  if (!summary || !summary.schedule_enabled || summary.schedule_cadence === "manual") return null;
  const rel = timeUntil(summary.next_scheduled_run_at);
  return rel ? `next auto-run ${rel}` : null;
}

function VisibilityScorePanel({ score }: { score: number | null }) {
  const circ = 2 * Math.PI * 48;
  return (
    <div className="panel">
      <div className="ph"><h3>Visibility score</h3></div>
      {score != null ? (
        <div className="score">
          <div className="scorering">
            <svg width="110" height="110">
              <circle cx="55" cy="55" r="48" fill="none" style={{ stroke: "var(--s4)" }} strokeWidth="9" />
              <circle
                cx="55" cy="55" r="48" fill="none" style={{ stroke: "var(--good)" }} strokeWidth="9" strokeLinecap="round"
                strokeDasharray={`${((score / 100) * circ).toFixed(1)} ${circ.toFixed(1)}`}
              />
            </svg>
            <div className="v"><b>{score.toFixed(0)}</b><span>/100</span></div>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.6 }}>
            Weighted: recommended 40%, neutral 15%, negative -10%, prominence 20%, sentiment 15%, coverage 20%.
            <br />Hollow citations excluded.
          </div>
        </div>
      ) : (
        <EmptyState>Score appears after the first completed run.</EmptyState>
      )}
    </div>
  );
}

function CitationTrendPanel({ summary, latestRate }: { summary: DashboardSummary | undefined; latestRate: number | null }) {
  const trend = (summary?.citation_rate_trend ?? []).map((p) => p.citation_rate * 100);
  const first = trend[0];
  const last = trend[trend.length - 1];
  const deltaPts = trend.length >= 2 ? Math.round(last - first) : null;

  return (
    <div className="panel">
      <div className="ph">
        <h3>Citation rate, last {trend.length || 0} runs</h3>
        <span className="note">hollow excluded</span>
        <div className="sp" />
        <span className="mono" style={{ fontSize: 20 }}>{pctFmt(latestRate)}</span>
        {deltaPts != null && deltaPts !== 0 && (
          <Chip tone={deltaPts > 0 ? "good" : "bad"}>
            {deltaPts > 0 ? "+" : ""}{deltaPts} pts vs {trend.length} runs ago
          </Chip>
        )}
      </div>
      {trend.length > 1 ? (
        <>
          <AreaChart vals={trend} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--ink5)", fontFamily: "var(--mono)", marginTop: 6 }}>
            <span>
              {summary?.citation_rate_trend?.[0]
                ? new Date(summary.citation_rate_trend[0].date).toLocaleDateString([], { day: "numeric", month: "short" })
                : ""}
            </span>
            <span>
              {summary?.citation_rate_trend?.length
                ? new Date(summary.citation_rate_trend[summary.citation_rate_trend.length - 1].date).toLocaleDateString([], { day: "numeric", month: "short" })
                : ""}
            </span>
          </div>
        </>
      ) : (
        <EmptyState>Not enough completed runs yet.</EmptyState>
      )}
    </div>
  );
}

export function DashboardHome() {
  const { display } = useAuth();
  const [runId, setRunId] = useState<string | null>(null);
  const [autoLoaded, setAutoLoaded] = useState(false);

  // Auto-load latest run once on mount
  const { data: latestRun, isSuccess: latestFetched } = useQuery({
    queryKey: ["latest-run"],
    queryFn: dashboard.getLatestRun,
    enabled: !autoLoaded,
  });

  useEffect(() => {
    if (!latestFetched) return;
    setAutoLoaded(true);
    if (latestRun?.run?.id) setRunId(latestRun.run.id);
  }, [latestFetched, latestRun?.run?.id]);

  // Poll run status while active
  const { data: runData } = useQuery<RunSummaryResponse>({
    queryKey: ["run", runId],
    queryFn: () => dashboard.getRunDetail(runId!),
    enabled: runId != null,
    refetchInterval: (q) => {
      const s = q.state.data?.run?.status;
      return s && ACTIVE.has(s) ? 2000 : false;
    },
  });

  const run = runData?.run;

  const { data: runPrompts } = useQuery({
    queryKey: ["run-prompts", runId],
    queryFn: () => dashboard.getRunPrompts(runId!),
    enabled: HAS_RESULTS.has(run?.status ?? ""),
  });

  const { data: summary } = useQuery<DashboardSummary>({
    queryKey: ["dashboard-summary"],
    queryFn: dashboard.getSummary,
    refetchInterval: 60_000, // keep the next-run countdown current
  });

  const nextRun = nextRunLabel(summary);
  const displayId = (run as { display_id?: string } | undefined)?.display_id ?? run?.id.slice(0, 8);

  if (run && (ACTIVE.has(run.status) || run.status === "failed")) {
    return (
      <>
        <div className="phead">
          <div className="grow">
            <h1 className="page">Dashboard</h1>
            <div className="sub">
              {run.status === "failed" ? "The latest run failed, our team is on it" : "A run is in progress, results will appear automatically when complete"}
            </div>
          </div>
        </div>
        <RunProgress run={run} />
      </>
    );
  }

  if (run && HAS_RESULTS.has(run.status) && runData) {
    return (
      <>
        <div className="phead">
          <div className="grow">
            <h1 className="page">Dashboard</h1>
            <div className="sub">
              Latest run {displayId}, {relTime(run.created_at)}
              {nextRun && <>, {nextRun}</>}
            </div>
          </div>
          {display.runs && (
            <Link className="btn sm" to={`runs/${run.id}`}>
              Open latest run <ArrowForwardRoundedIcon style={{ fontSize: 13 }} />
            </Link>
          )}
        </div>

        {display.score && display.trend ? (
          <div className="grid2" style={{ gridTemplateColumns: "1fr 1.4fr" }}>
            <VisibilityScorePanel score={summary?.visibility_score ?? null} />
            <CitationTrendPanel summary={summary} latestRate={runData.overall_citation_rate} />
          </div>
        ) : display.score ? (
          <VisibilityScorePanel score={summary?.visibility_score ?? null} />
        ) : display.trend ? (
          <CitationTrendPanel summary={summary} latestRate={runData.overall_citation_rate} />
        ) : null}

        {display.status && Object.keys(runData.platform_errors ?? {}).length > 0 && (
          <PlatformErrorBanner errors={runData.platform_errors} />
        )}

        <SummaryCards summary={runData} display={display} />

        {display.prompts && runPrompts && runPrompts.length > 0 && (
          <PromptTable prompts={runPrompts} showResponses={display.responses} showModelIds={display.model_ids} />
        )}

        <div className="footer-note">
          Data refreshes automatically after every engine run, human-reviewed before anything is published.
        </div>
      </>
    );
  }

  // Empty state — no completed runs
  if (autoLoaded && !runId) {
    return (
      <>
        <div className="phead">
          <div className="grow">
            <h1 className="page">Dashboard</h1>
            <div className="sub">Your AI visibility monitoring is being set up</div>
          </div>
        </div>
        <div className="panel">
          <div className="emptystate" style={{ padding: "64px 44px" }}>
            <div style={{ width: 40, margin: "0 auto 16px", color: "var(--ink3)" }}>
              <OrigoMark size={40} />
            </div>
            <p style={{ fontSize: 14, fontWeight: 600, color: "var(--ink1)" }}>
              Your AI visibility monitoring is being set up
            </p>
            <p style={{ marginTop: 6 }}>
              Your first report will appear here once the initial analysis runs.
              {nextRun && <> Next auto-run {nextRun.replace("next auto-run ", "")}.</>}
            </p>
          </div>
        </div>
      </>
    );
  }

  return <EmptyState>Loading...</EmptyState>;
}
