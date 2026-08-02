import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import { runsApi, costApi, clientsApi } from "../../api/client";
import type { RunListResponse, RunMode, RunRead, RunStatsPeriod, RunSummaryItem } from "../../types";
import { BarMeter, EmptyState, PillRow, RunStatusChip, fmtMs, pctFmt, relTime, usdFmt, useConfirm, useToast } from "../ui/ui";

const ACTIVE = new Set(["pending", "running"]);
// Statuses an admin can cancel: in-flight, or a staged run awaiting analysis.
const CANCELLABLE = new Set(["pending", "running"]);

const PERIOD_LABELS: Record<RunStatsPeriod, string> = { today: "Today", "7d": "7d", "30d": "30d", "90d": "90d" };

// Actual engine working time. Staged runs sit idle between admin clicks, so
// updated_at - created_at overstates them — prefer the per-phase sum.
// Collection and analysis overlap (each response is analyzed as it lands), so
// when the engine recorded their combined elapsed time, use that instead of
// adding the two phases together and double-counting the overlap.
function workedMs(run: RunSummaryItem): number | null {
  const t = run.phase_timings;
  if (t) {
    const collectAnalyze =
      t.collection_analysis_ms ?? (t.monitoring_ms ?? 0) + (t.analysis_ms ?? 0);
    const sum = collectAnalyze + (t.generation_ms ?? 0);
    if (sum > 0) return sum;
  }
  const diff = new Date(run.updated_at).getTime() - new Date(run.created_at).getTime();
  return diff > 0 ? diff : null;
}

function fmtDurationSecs(seconds: number): string {
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// The freshly created run, shaped like a list row so it can be shown before
// the list refetch lands.
function toListItem(run: RunRead): RunSummaryItem {
  return {
    id: run.id,
    display_id: null,
    status: run.status,
    generation_status: run.generation_status ?? null,
    total_prompts: run.total_prompts,
    completed_prompts: run.completed_prompts,
    created_at: run.created_at,
    updated_at: run.updated_at,
    overall_citation_rate: null,
    cost_usd: null,
    phase_timings: run.phase_timings ?? null,
  };
}

type Phase = "collect" | "analyze" | "recommend";

function livePhase(run: RunSummaryItem): Phase {
  if (run.completed_prompts < run.total_prompts) return "collect";
  if (run.generation_status === "running") return "recommend";
  return "analyze";
}

const PHASE_LABEL: Record<Phase, string> = {
  collect: "Collecting AI responses",
  analyze: "Analyzing responses",
  recommend: "Generating recommendations",
};

function StepState({ n, label, state }: { n: number; label: string; state: "" | "now" | "done" }) {
  return (
    <div className={`step ${state}`}>
      <span className="sd">{n}</span>
      <span className="sl">{label}</span>
    </div>
  );
}

function LiveRunBanner({ run, onCancel, cancelling }: {
  run: RunSummaryItem; onCancel: () => void; cancelling: boolean;
}) {
  const { clientId } = useParams<{ clientId: string }>();
  const phase = livePhase(run);
  const stepC = phase === "collect" ? "now" : "done";
  const stepA = phase === "analyze" ? "now" : phase === "recommend" ? "done" : "";
  const stepR = phase === "recommend" ? "now" : "";

  // Live spend ticks while the run works (R5).
  const { data: cost } = useQuery({
    queryKey: ["admin-run-costs", clientId, run.id],
    queryFn: () => costApi.getRunCosts(clientId!, run.id),
    refetchInterval: 5000,
  });

  return (
    <div className="banner live">
      <span className="bi" style={{ color: "var(--white)" }}>
        <PlayArrowRoundedIcon style={{ fontSize: 16 }} />
      </span>
      <div style={{ flex: 1 }}>
        <b>Run in progress</b>{" "}
        <span className="mono dim" style={{ fontSize: 11, marginLeft: 6 }}>{run.display_id ?? run.id.slice(0, 12)}</span>
        <div className="stepper" style={{ marginTop: 9 }}>
          <StepState n={1} label="Collect" state={stepC} />
          <div className={`step-line ${stepC === "done" ? "done" : ""}`} />
          <StepState n={2} label="Analyze" state={stepA as "" | "now" | "done"} />
          <div className={`step-line ${stepR ? "done" : ""}`} />
          <StepState n={3} label="Recommend" state={stepR as "" | "now"} />
          <span style={{ marginLeft: 16, fontSize: 12, color: "var(--ink3)" }}>{PHASE_LABEL[phase]}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 9 }}>
          <BarMeter pct={run.total_prompts > 0 ? (run.completed_prompts / run.total_prompts) * 100 : 0} width={420} />
          <span className="mono" style={{ fontSize: 11.5 }}>{run.completed_prompts}/{run.total_prompts}</span>
          {cost?.total_cost_usd != null && (
            <span className="mono dim" style={{ fontSize: 11.5 }}>live spend {usdFmt(cost.total_cost_usd)}</span>
          )}
        </div>
      </div>
      <button className="btn sm danger" onClick={onCancel} disabled={cancelling}>
        <CloseRoundedIcon style={{ fontSize: 13 }} /> Cancel run
      </button>
    </div>
  );
}

export function ClientRuns() {
  const { clientId } = useParams<{ clientId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [page, setPage] = useState(1);
  const [costPeriod, setCostPeriod] = useState<RunStatsPeriod>("30d");
  // Pre-run recommendations toggle. On: recommendations generate automatically
  // once analysis finishes. Off: the run stops after analysis and an admin
  // presses Generate recommendations on the run. Null until the client's own
  // default loads, so the checkbox never flips under the admin after a fetch.
  const [genRecsOverride, setGenRecsOverride] = useState<boolean | null>(null);
  // Synchronous re-entry guard: React state (isPending) only updates on the
  // next render, so a double click in the same tick could enqueue two runs.
  const startingRef = useRef(false);

  // Only for the recommendations toggle's default.
  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });
  const genRecs = genRecsOverride ?? client?.auto_generate_recommendations ?? true;

  const { data, isLoading } = useQuery({
    queryKey: ["admin-runs", clientId, page],
    queryFn: () => runsApi.list(clientId!, page),
    refetchInterval: (query) => {
      const items: RunSummaryItem[] = query.state.data?.items ?? [];
      return items.some((r) => ACTIVE.has(r.status)) ? 3000 : false;
    },
  });

  // Windowed cost + P95 duration for the selected period.
  const { data: runStats } = useQuery({
    queryKey: ["admin-client-run-stats", clientId, costPeriod],
    queryFn: () => costApi.getClientRunStats(clientId!, costPeriod),
    enabled: !!clientId,
  });

  const triggerMut = useMutation({
    mutationFn: (mode: RunMode) => runsApi.trigger(clientId!, mode, genRecs),
    onSuccess: (newRun, mode) => {
      // Insert the created run into every cached run list right away so the
      // trigger buttons stay disabled during the refetch window — otherwise
      // they re-enable for a moment and a second parallel run can be started.
      qc.setQueriesData<RunListResponse>({ queryKey: ["admin-runs", clientId] }, (old) => {
        if (!old || !Array.isArray(old.items)) return old;
        if (old.items.some((r) => r.id === newRun.id)) return old;
        return { ...old, items: [toListItem(newRun), ...old.items], total: old.total + 1 };
      });
      qc.invalidateQueries({ queryKey: ["admin-runs", clientId] });
      const recsNote = genRecs ? "" : ", recommendations on demand";
      toast(
        mode === "staged"
          ? `Prompt run enqueued, responses only${recsNote}`
          : `Analyze run enqueued, full pipeline${recsNote}`,
      );
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to start run", "err"),
  });

  // Staged runs: advance a parked run (responses_ready) into analysis.
  const analyzeMut = useMutation({
    mutationFn: (runId: string) => runsApi.analyze(clientId!, runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-runs", clientId] });
      toast("Analysis started");
    },
  });

  // Kill switch (R4): cancel an in-flight run straight from the list.
  const cancelMut = useMutation({
    mutationFn: (runId: string) => runsApi.cancel(clientId!, runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-runs", clientId] });
      toast("Run cancelled");
    },
  });
  async function confirmCancel(runId: string, discard = false) {
    const ok = await confirm(
      discard
        ? {
            title: "Discard this run?",
            message: "Its collected responses will never be analyzed. No new API calls will be made.",
            confirmLabel: "Discard run",
            danger: true,
          }
        : {
            title: "Cancel this run?",
            message: "No new API calls will be made; work done so far is kept.",
            confirmLabel: "Cancel run",
            cancelLabel: "Keep running",
            danger: true,
          },
    );
    if (ok) cancelMut.mutate(runId);
  }

  // Immediate, race-free trigger: the ref blocks same-tick re-entry, the
  // optimistic list insert keeps the buttons disabled until refetch.
  function startRun(mode: RunMode) {
    if (startingRef.current || triggerMut.isPending) return;
    startingRef.current = true;
    triggerMut.mutate(mode, { onSettled: () => { startingRef.current = false; } });
  }

  const items = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1;
  const liveRun = items.find(
    (r) => ACTIVE.has(r.status) || (["completed", "partial"].includes(r.status) && r.generation_status === "running")
  );
  const hasActive = !!liveRun || items.some((r) => ACTIVE.has(r.status));

  const completedCount = items.filter((r) => r.status === "completed").length;
  const partialCount = items.filter((r) => r.status === "partial").length;
  const failedCount = items.filter((r) => r.status === "failed").length;
  const cancelledCount = items.filter((r) => r.status === "cancelled").length;
  const durations = items
    .filter((r) => ["completed", "partial"].includes(r.status))
    .map((r) => workedMs(r))
    .filter((v): v is number => v != null);
  const avgDurationMs = durations.length ? durations.reduce((s, v) => s + v, 0) / durations.length : null;

  const totalCost = runStats?.total_cost_usd ?? null;
  const avgPerRun = runStats && runStats.run_count > 0 ? runStats.total_cost_usd / runStats.run_count : null;

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--good)" }} />Completed</div>
          <div className="val">{completedCount}</div>
          <div className="hint">{partialCount > 0 ? `${partialCount} partial (dropped calls)` : "100% success"}</div>
        </div>
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--bad)" }} />Failed</div>
          <div className="val">{failedCount}</div>
          <div className="hint">{cancelledCount} cancelled</div>
        </div>
        <div className="card">
          <div className="lbl">Total cost ({PERIOD_LABELS[costPeriod]})</div>
          <div className="val">{totalCost != null ? `$${totalCost.toFixed(2)}` : "-"}</div>
          <div className="hint">{avgPerRun != null ? `~$${avgPerRun.toFixed(0)}/run over ${runStats!.run_count} runs` : "no runs in the window"}</div>
        </div>
        <div className="card">
          <div className="lbl">Avg duration</div>
          <div className="val">{avgDurationMs != null ? fmtMs(avgDurationMs) : "-"}</div>
          <div className="hint">
            {runStats?.p95_duration_seconds != null
              ? `P95 ${fmtDurationSecs(runStats.p95_duration_seconds)}, target 15m per 100 prompts`
              : "target 15m per 100 prompts"}
          </div>
        </div>
      </div>

      {liveRun && (
        <LiveRunBanner run={liveRun} onCancel={() => confirmCancel(liveRun.id)} cancelling={cancelMut.isPending} />
      )}

      <div className="panel" style={{ padding: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)", flexWrap: "wrap" }}>
          <h3 style={{ fontSize: 13.5, fontWeight: 650 }}>Run history</h3>
          <span style={{ color: "var(--ink4)", fontSize: 11.5 }}>showing {items.length}</span>
          <div style={{ flex: 1 }} />
          <PillRow
            value={costPeriod}
            onChange={setCostPeriod}
            options={(["today", "7d", "30d", "90d"] as const).map((p) => ({ value: p, label: PERIOD_LABELS[p] }))}
          />
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink3)", cursor: "pointer" }}
            title="On: recommendations are generated automatically once analysis completes. Off: the run finishes after analysis and you generate them on demand."
          >
            <input
              type="checkbox"
              checked={genRecs}
              onChange={(e) => setGenRecsOverride(e.target.checked)}
              style={{ cursor: "pointer" }}
            />
            Recommendations
          </label>
          <button
            className="btn"
            disabled={triggerMut.isPending || hasActive}
            title="Collect AI responses only; run analysis and recommendations later, one click each"
            onClick={() => startRun("staged")}
          >
            <DownloadRoundedIcon style={{ fontSize: 14 }} /> Prompt run
          </button>
          <button
            className="btn pri"
            disabled={triggerMut.isPending || hasActive}
            onClick={() => startRun("full")}
          >
            <PlayArrowRoundedIcon style={{ fontSize: 15 }} /> {hasActive ? "Run in progress..." : "Analyze run"}
          </button>
        </div>

        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : items.length === 0 ? (
          <EmptyState>No runs yet. Trigger the first run above.</EmptyState>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th className="right">Citation</th>
                  <th className="right">Cost</th>
                  <th className="right">Duration</th>
                  <th>Started</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((run) => (
                  <tr key={run.id} className="rowlink" onClick={() => navigate(`/clients/${clientId}/runs/${run.id}`)}>
                    <td className="mono" style={{ fontSize: 12 }}>{run.display_id ?? run.id.slice(0, 8)}</td>
                    <td><RunStatusChip status={run.status} /></td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                        <BarMeter pct={(run.completed_prompts / Math.max(run.total_prompts, 1)) * 100} width={70} />
                        <span className="mono dim2" style={{ fontSize: 11 }}>{run.completed_prompts}/{run.total_prompts}</span>
                      </div>
                    </td>
                    <td className="right mono">{pctFmt(run.overall_citation_rate)}</td>
                    <td className="right mono">{usdFmt(run.cost_usd)}</td>
                    <td className="right mono">{ACTIVE.has(run.status) ? "..." : fmtMs(workedMs(run))}</td>
                    <td className="dim2">{relTime(run.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {run.status === "responses_ready" ? (
                        <span style={{ display: "inline-flex", gap: 6 }}>
                          <button className="btn sm pri" disabled={analyzeMut.isPending} onClick={() => analyzeMut.mutate(run.id)}>
                            <PlayArrowRoundedIcon style={{ fontSize: 13 }} /> Analyze
                          </button>
                          <button className="btn sm danger" disabled={cancelMut.isPending} onClick={() => confirmCancel(run.id, true)}>
                            <CloseRoundedIcon style={{ fontSize: 12 }} /> Discard
                          </button>
                        </span>
                      ) : CANCELLABLE.has(run.status) ? (
                        <button className="btn sm danger" disabled={cancelMut.isPending} onClick={() => confirmCancel(run.id)}>
                          <CloseRoundedIcon style={{ fontSize: 12 }} /> Cancel
                        </button>
                      ) : (
                        <span className="dim"><ChevronRightRoundedIcon style={{ fontSize: 14 }} /></span>
                      )}
                    </td>
                  </tr>
                ))}
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
