import { useQuery } from "@tanstack/react-query";
import { useParams, Link, useNavigate } from "react-router-dom";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { clientsApi, costApi, recommendationsApi, runsApi } from "../../api/client";
import type { RecommendationListItem, RunSummaryItem } from "../../types";
import { AreaChart, Donut } from "../ui/charts";
import { EmptyState, RunStatusChip, fmtMs, pctFmt, platMeta, usdFmt } from "../ui/ui";
import { RecCard } from "../recommendations/RecCard";

// Terminal statuses that carry viewable results (partial = finished with drops).
const HAS_RESULTS = new Set(["completed", "partial"]);

const PRIORITY_WEIGHT: Record<string, number> = { high: 0, medium: 1, low: 2 };

// Actual engine working time. Staged runs sit idle between admin clicks, so
// updated_at - created_at overstates them — prefer the per-phase sum.
function workedMs(run: RunSummaryItem): number | null {
  const t = run.phase_timings;
  if (t) {
    const sum = (t.monitoring_ms ?? 0) + (t.analysis_ms ?? 0) + (t.generation_ms ?? 0);
    if (sum > 0) return sum;
  }
  const diff = new Date(run.updated_at).getTime() - new Date(run.created_at).getTime();
  return diff > 0 ? diff : null;
}

export function ClientOverview() {
  const { clientId } = useParams<{ clientId: string }>();
  const navigate = useNavigate();

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const { data: runs } = useQuery({
    queryKey: ["admin-runs", clientId, "overview"],
    queryFn: () => runsApi.list(clientId!, 1, 50),
    enabled: !!clientId,
  });

  const { data: runStats } = useQuery({
    queryKey: ["admin-client-run-stats", clientId, "30d"],
    queryFn: () => costApi.getClientRunStats(clientId!, "30d"),
    enabled: !!clientId,
  });

  const { data: recSummary } = useQuery({
    queryKey: ["rec-summary", clientId],
    queryFn: () => recommendationsApi.summary(clientId!),
    enabled: !!clientId,
  });

  const { data: pendingRecs } = useQuery({
    queryKey: ["client-recs", clientId, "pending-top"],
    queryFn: () => recommendationsApi.list(clientId!, { status: "pending", per_page: 50 }),
    enabled: !!clientId,
  });

  const lastRun = runs?.items[0];
  const lastResultRun = runs?.items.find((r) => HAS_RESULTS.has(r.status));

  const { data: latestRunSummary } = useQuery({
    queryKey: ["admin-run-detail", clientId, lastResultRun?.id],
    queryFn: () => runsApi.get(clientId!, lastResultRun!.id),
    enabled: !!clientId && !!lastResultRun?.id,
  });

  if (!client) return <EmptyState>Loading...</EmptyState>;

  const pending = recSummary?.by_status?.pending ?? 0;
  const latestRate = lastResultRun?.overall_citation_rate ?? null;

  const trend = (runs?.items ?? [])
    .slice()
    .reverse()
    .map((r) => r.overall_citation_rate)
    .filter((v): v is number => v != null)
    .map((v) => v * 100);

  const platformStats = latestRunSummary?.platform_stats ?? [];
  const totalCited = platformStats.reduce((s, p) => s + p.cited_count, 0);

  const topPending: RecommendationListItem | undefined = (pendingRecs?.items ?? [])
    .slice()
    .sort((a, b) =>
      (PRIORITY_WEIGHT[a.priority] ?? 3) - (PRIORITY_WEIGHT[b.priority] ?? 3) ||
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )[0];

  const openRec = (rec: RecommendationListItem) =>
    navigate(`/clients/${clientId}/recommendations?rec=${rec.id}`);

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="lbl">Citation rate</div>
          <div className="val">{pctFmt(latestRate)}</div>
          <div className="hint">latest run, hollow excluded</div>
        </div>
        <div className="card">
          <div className="lbl">Prompts</div>
          <div className="val">
            {client.total_prompts}
          </div>
          <div className="hint">total tracked, cap 100, ~50 recommended</div>
        </div>
        <div className="card">
          <div className="lbl">Cost (30d)</div>
          <div className="val">${(runStats?.total_cost_usd ?? 0).toFixed(0)}</div>
          <div className="hint">
            {runStats && runStats.run_count > 0
              ? `${runStats.run_count} runs, avg $${(runStats.total_cost_usd / runStats.run_count).toFixed(2)}`
              : "no runs in the window"}
          </div>
        </div>
        <div className="card">
          <div className="lbl">Needs review</div>
          <div className="val" style={{ color: pending > 0 ? "var(--warn)" : "var(--white)" }}>{pending}</div>
          <div className="hint">
            <Link to={`/clients/${clientId}/recommendations`} style={{ color: "var(--ink3)", textDecoration: "underline", display: "inline-flex", alignItems: "center", gap: 2 }}>
              open review queue <ArrowForwardRoundedIcon style={{ fontSize: 11 }} />
            </Link>
          </div>
        </div>
      </div>

      <div className="grid31">
        <div className="panel">
          <div className="ph">
            <h3>Citation rate by run</h3>
            <span className="note">hollow citations excluded</span>
          </div>
          {trend.length > 1 ? <AreaChart vals={trend} /> : <EmptyState>Not enough completed runs yet.</EmptyState>}
        </div>
        <div className="panel">
          <div className="ph">
            <h3>Platform mix</h3>
            <span className="note">latest run</span>
          </div>
          {platformStats.length > 0 && totalCited > 0 ? (
            <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <Donut segs={platformStats.map((p) => ({ v: p.cited_count, c: platMeta(p.platform).c }))} size={120} hole={38} />
              <div style={{ flex: 1 }}>
                {platformStats.map((p) => (
                  <div key={p.platform} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 7 }}>
                    <span style={{ width: 7, height: 7, borderRadius: 99, background: platMeta(p.platform).c, flexShrink: 0 }} />
                    {platMeta(p.platform).label}
                    <span className="mono dim" style={{ marginLeft: "auto" }}>
                      {totalCited > 0 ? `${Math.round((p.cited_count / totalCited) * 100)}%` : "-"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState>{lastResultRun ? "No citations in the latest run." : "No run data yet."}</EmptyState>
          )}
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph">
            <h3>Latest run</h3>
            <div className="sp" />
            {lastRun && <RunStatusChip status={lastRun.status} />}
          </div>
          {lastRun ? (
            <>
              <div className="kv" style={{ marginBottom: 12 }}>
                <span className="k">Run</span>
                <span className="mono">{lastRun.display_id ?? lastRun.id.slice(0, 12)}</span>
                <span className="k">Progress</span>
                <span className="mono">{lastRun.completed_prompts}/{lastRun.total_prompts} prompts</span>
                <span className="k">Citation</span>
                <span className="mono">{pctFmt(lastRun.overall_citation_rate)}</span>
                <span className="k">Cost</span>
                <span className="mono">{usdFmt(lastRun.cost_usd)}</span>
                <span className="k">Duration</span>
                <span className="mono">{fmtMs(workedMs(lastRun))}</span>
              </div>
              <Link className="btn sm" to={`/clients/${clientId}/runs/${lastRun.id}`}>
                View run <ArrowForwardRoundedIcon style={{ fontSize: 13 }} />
              </Link>
            </>
          ) : (
            <EmptyState>No runs yet.</EmptyState>
          )}
        </div>

        <div className="panel">
          <div className="ph">
            <h3>Top pending recommendation</h3>
            <div className="sp" />
            <Link className="btn sm" to={`/clients/${clientId}/recommendations`}>
              Queue <ArrowForwardRoundedIcon style={{ fontSize: 13 }} />
            </Link>
          </div>
          {topPending ? (
            <RecCard rec={topPending} onOpen={openRec} clientPath={clientId} />
          ) : (
            <EmptyState>Review queue is clear.</EmptyState>
          )}
        </div>
      </div>
    </>
  );
}
