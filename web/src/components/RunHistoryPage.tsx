import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import { dashboard } from "../lib/api";
import type { RunListItem } from "../lib/api";
import { useAuth } from "../auth/AuthContext";
import { BarMeter, EmptyState, RunStatusChip, pctFmt, relTime, usdFmt } from "./ui";

const ACTIVE = new Set(["pending", "running"]);
// Terminal statuses that carry viewable results (partial = finished with drops).
const HAS_RESULTS = new Set(["completed", "partial"]);
// With run status hidden from the client, anything negative (cancelled/failed)
// stays behind the wall entirely rather than showing a status-less row.
const HIDE_WHEN_NO_STATUS = new Set(["cancelled", "failed"]);

// With run IDs hidden, clients see a friendly date label instead of the
// internal display id.
function runLabel(run: RunListItem, showRunIds: boolean): string {
  if (showRunIds) return run.display_id ?? run.id.slice(0, 8);
  const d = new Date(run.created_at.endsWith("Z") ? run.created_at : run.created_at + "Z");
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} run`;
}

// Actual engine working time. Staged runs sit idle between admin clicks, so
// updated_at - created_at overstates them — prefer the per-phase sum.
function fmtDuration(run: RunListItem): string {
  const t = run.phase_timings;
  const phaseSum = t ? (t.monitoring_ms ?? 0) + (t.analysis_ms ?? 0) + (t.generation_ms ?? 0) : 0;
  const diff = phaseSum > 0
    ? phaseSum
    : run.updated_at
      ? new Date(run.updated_at).getTime() - new Date(run.created_at).getTime()
      : 0;
  if (diff <= 0) return "-";
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function RunHistoryPage() {
  const { display } = useAuth();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-runs", page],
    queryFn: () => dashboard.getRuns(page),
    refetchInterval: (q) => {
      const runs: RunListItem[] = q.state.data?.runs ?? [];
      return runs.some((r) => ACTIVE.has(r.status)) ? 3000 : false;
    },
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1;
  const rows = (data?.runs ?? []).filter((r) => display.status || !HIDE_WHEN_NO_STATUS.has(r.status));
  const promptCount = rows[0]?.total_prompts;

  return (
    <>
      <div className="phead">
        <div className="grow">
          <h1 className="page">Run history</h1>
          <div className="sub">
            {data ? `${data.total} runs` : "Loading runs"}
            {promptCount ? `, every run is a full sweep of your ${promptCount} prompts across 4 AI platforms` : ""}
          </div>
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : !rows.length ? (
          <EmptyState>No runs yet.</EmptyState>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th>Run</th>
                  {display.status && <th>Status</th>}
                  {display.progress && <th>Progress</th>}
                  <th className="right">Citation rate</th>
                  {display.cost && <th className="right">Cost</th>}
                  {display.duration && <th className="right">Duration</th>}
                  <th>Date</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => {
                  const viewable = HAS_RESULTS.has(run.status);
                  return (
                    <tr
                      key={run.id}
                      className={viewable ? "rowlink" : undefined}
                      onClick={viewable ? () => navigate(`/dashboard/runs/${run.id}`) : undefined}
                    >
                      <td className={display.run_ids ? "mono" : undefined} style={{ fontSize: 12 }}>{runLabel(run, display.run_ids)}</td>
                      {display.status && <td><RunStatusChip status={run.status} /></td>}
                      {display.progress && (
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                            <BarMeter pct={(run.completed_prompts / Math.max(run.total_prompts, 1)) * 100} width={64} />
                            <span className="mono dim2" style={{ fontSize: 11 }}>{run.completed_prompts}/{run.total_prompts}</span>
                          </div>
                        </td>
                      )}
                      <td className="right">
                        <span
                          className="mono"
                          style={{
                            fontSize: 13,
                            color: run.overall_citation_rate == null ? "var(--ink4)" :
                              run.overall_citation_rate >= 0.3 ? "var(--good)" : "var(--warn)",
                          }}
                        >
                          {pctFmt(run.overall_citation_rate)}
                        </span>
                      </td>
                      {display.cost && <td className="right mono">{usdFmt(run.cost_usd)}</td>}
                      {display.duration && <td className="right mono">{ACTIVE.has(run.status) ? "..." : fmtDuration(run)}</td>}
                      <td className="dim2">{relTime(run.created_at)}</td>
                      <td className="dim">
                        {viewable && <ChevronRightRoundedIcon style={{ fontSize: 14 }} />}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderTop: "1px solid var(--bf)" }}>
            <button className="btn sm" disabled={page === 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <ChevronLeftRoundedIcon style={{ fontSize: 14 }} /> Prev
            </button>
            <span className="mono dim" style={{ fontSize: 11 }}>Page {page} of {totalPages}</span>
            <button className="btn sm" disabled={page === totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
              Next <ChevronRightRoundedIcon style={{ fontSize: 14 }} />
            </button>
          </div>
        )}
      </div>
    </>
  );
}
