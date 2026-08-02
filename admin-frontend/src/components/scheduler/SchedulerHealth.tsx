import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import PauseRoundedIcon from "@mui/icons-material/PauseRounded";
import { clientsApi, scheduleApi } from "../../api/client";
import type { ClientSummary } from "../../types";
import { Donut } from "../ui/charts";
import { Chip, EmptyState, Modal, useToast } from "../ui/ui";

function relTime(iso: string | null) {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

function lastRefreshedLabel(tsMs: number): string {
  if (!tsMs) return "-";
  const s = Math.floor((Date.now() - tsMs) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  return `${Math.floor(s / 60)}m ago`;
}

// Countdown to the next scheduler tick (37s cadence).
function useNextTickCountdown(lastTickAt: string | null): string {
  const [label, setLabel] = useState("~37s");
  useEffect(() => {
    function compute() {
      if (!lastTickAt) return setLabel("~37s");
      const elapsed = Math.floor((Date.now() - new Date(lastTickAt).getTime()) / 1000);
      const remaining = Math.max(0, 37 - (elapsed % 37));
      setLabel(`in ${remaining}s`);
    }
    compute();
    const id = setInterval(compute, 1000);
    return () => clearInterval(id);
  }, [lastTickAt]);
  return label;
}

const POOL_META: Array<{ key: string; label: string; c: string }> = [
  { key: "completed", label: "Completed", c: "var(--good)" },
  { key: "started", label: "Started", c: "var(--ink2)" },
  { key: "enqueued", label: "Queued", c: "var(--warn)" },
  { key: "failed", label: "Failed", c: "var(--bad)" },
  { key: "skipped", label: "Skipped", c: "var(--ink5)" },
];

export function SchedulerHealth() {
  const qc = useQueryClient();
  const toast = useToast();
  const [pauseReason, setPauseReason] = useState("");
  const [pauseConfirmText, setPauseConfirmText] = useState("");
  const [showPauseModal, setShowPauseModal] = useState(false);

  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["scheduler-health"],
    queryFn: () => scheduleApi.health(),
    refetchInterval: 10_000,
  });

  const { data: clients = [] } = useQuery({
    queryKey: ["admin-clients", ""],
    queryFn: () => clientsApi.list(""),
    staleTime: 60_000,
  });
  const enrolled = (clients as ClientSummary[]).filter((c) => c.schedule_enabled).length;

  const pauseAllMut = useMutation({
    mutationFn: () => scheduleApi.pauseAll(pauseReason),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["scheduler-health"] });
      qc.invalidateQueries({ queryKey: ["admin-clients"] });
      toast(`Paused ${res.paused_count} client schedule${res.paused_count !== 1 ? "s" : ""}`);
      setShowPauseModal(false);
      setPauseReason("");
      setPauseConfirmText("");
    },
  });

  const nextTickIn = useNextTickCountdown(data?.last_tick_at ?? null);

  if (isLoading) return <EmptyState>Loading scheduler health...</EmptyState>;

  const healthy = data?.is_healthy ?? false;
  const today = data?.scheduled_runs_today ?? {};
  const poolTotal = POOL_META.reduce((s, m) => s + (today[m.key] ?? 0), 0);

  return (
    <>
      <div className="phead">
        <div className="grow">
          <h1 className="page">Scheduler</h1>
          <div className="sub">Last refreshed {lastRefreshedLabel(dataUpdatedAt)}, tick every 37s</div>
        </div>
        <button className="btn danger" onClick={() => setShowPauseModal(true)}>
          <PauseRoundedIcon style={{ fontSize: 15 }} /> Pause all schedules
        </button>
      </div>

      <div className="cards">
        <div className="card">
          <div className="lbl">
            <span className="pd" style={{ background: healthy ? "var(--good)" : "var(--bad)" }} />Engine health
          </div>
          <div className="val" style={{ fontSize: 20, color: healthy ? "var(--good)" : "var(--bad)" }}>
            {healthy ? "Healthy" : "Unhealthy"}
          </div>
          <div className="hint">last tick {relTime(data?.last_tick_at ?? null)}</div>
        </div>
        <div className="card">
          <div className="lbl">Enrolled clients</div>
          <div className="val">
            {enrolled}<span style={{ fontSize: 14, color: "var(--ink4)" }}>/{clients.length}</span>
          </div>
          <div className="hint">schedules enabled</div>
        </div>
        <div className="card">
          <div className="lbl">Enqueued today</div>
          <div className="val">{today.enqueued ?? 0}</div>
          <div className="hint">queued by the scheduler</div>
        </div>
        <div className="card">
          <div className="lbl">Failed today</div>
          <div className="val" style={(today.failed ?? 0) > 0 ? { color: "var(--bad)" } : undefined}>{today.failed ?? 0}</div>
          <div className="hint">{data?.consecutive_failures ?? 0} consecutive tick failures</div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph">
            <h3>Tick health</h3>
            <span className="note">target: a tick at least every 60s</span>
            <div className="sp" />
            {healthy ? <Chip tone="good" live>Ticking</Chip> : <Chip tone="bad">Stalled</Chip>}
          </div>
          <div style={{ display: "flex", gap: 26, marginBottom: 12, flexWrap: "wrap" }}>
            {[
              { label: "LAST TICK", value: relTime(data?.last_tick_at ?? null) },
              { label: "TICK AGE", value: data?.last_tick_age_seconds != null ? `${Math.round(data.last_tick_age_seconds)}s` : "-" },
              { label: "ACTIVE CLIENTS", value: data?.active_clients_count ?? 0 },
            ].map(({ label, value }) => (
              <div key={label}>
                <div className="mono" style={{ fontSize: 10, letterSpacing: ".1em", color: "var(--ink4)" }}>{label}</div>
                <div className="mono" style={{ fontSize: 20 }}>{value}</div>
              </div>
            ))}
          </div>
          {data?.last_error && (
            <div className="banner bad" style={{ marginBottom: 0 }}>
              <div>
                <b>Last error</b>
                <div className="note mono">{data.last_error}</div>
              </div>
            </div>
          )}
        </div>

        <div className="panel">
          <div className="ph">
            <h3>Run pool</h3>
            <span className="note">scheduled runs today</span>
          </div>
          {poolTotal > 0 ? (
            <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
              <Donut segs={POOL_META.map((m) => ({ v: today[m.key] ?? 0, c: m.c }))} size={130} hole={42} />
              <div style={{ flex: 1 }}>
                {POOL_META.map((m) => (
                  <div key={m.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, marginBottom: 8 }}>
                    <span style={{ width: 7, height: 7, borderRadius: 99, background: m.c, flexShrink: 0 }} />
                    {m.label}
                    <span className="mono dim" style={{ marginLeft: "auto" }}>{today[m.key] ?? 0}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState>No scheduled runs today yet.</EmptyState>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="ph"><h3>Last tick</h3></div>
        <div className="kv">
          <span className="k">Clients evaluated</span>
          <span className="mono">{data?.last_tick_clients_evaluated ?? "-"}</span>
          <span className="k">Runs enqueued</span>
          <span className="mono">{data?.last_tick_runs_enqueued ?? "-"}</span>
          <span className="k">Tick at</span>
          <span className="mono">
            {data?.last_tick_at
              ? new Date(data.last_tick_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
              : "-"}
          </span>
          <span className="k">Next tick</span>
          <span className="mono">{nextTickIn}</span>
        </div>
      </div>

      {showPauseModal && (
        <Modal onClose={() => { setShowPauseModal(false); setPauseReason(""); setPauseConfirmText(""); }}>
          <h3 style={{ color: "var(--bad)" }}>Pause ALL schedules</h3>
          <div className="ms">Emergency stop for every client schedule. Manual runs stay possible.</div>
          <div className="fld">
            <label>Reason *</label>
            <input
              value={pauseReason}
              onChange={(e) => setPauseReason(e.target.value)}
              placeholder="e.g. provider incident"
            />
          </div>
          <div className="fld">
            <label>Type PAUSE ALL to confirm</label>
            <input
              value={pauseConfirmText}
              onChange={(e) => setPauseConfirmText(e.target.value)}
              style={{ fontFamily: "var(--mono)" }}
            />
          </div>
          <div className="macts">
            <button className="btn" onClick={() => { setShowPauseModal(false); setPauseReason(""); setPauseConfirmText(""); }}>
              Cancel
            </button>
            <button
              className="btn danger"
              disabled={pauseConfirmText !== "PAUSE ALL" || !pauseReason.trim() || pauseAllMut.isPending}
              onClick={() => pauseAllMut.mutate()}
            >
              {pauseAllMut.isPending ? "Pausing..." : "Pause all"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
