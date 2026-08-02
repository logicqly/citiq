import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { scheduleApi, clientsApi } from "../../api/client";
import type { ScheduleCadence, ScheduleConfig } from "../../types";
import { Chip, EmptyState, PillRow, RunStatusChip, TSwitch, useToast } from "../ui/ui";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function timeLabel(hour: number, minute: number) {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function relTimePast(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function timeUntil(iso: string | null): string {
  if (!iso) return "-";
  const diff = new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime() - Date.now();
  if (diff <= 0) return "now";
  const m = Math.floor(diff / 60000);
  if (m < 60) return `in ${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `in ${h}h ${m % 60}m`;
  return `in ${Math.floor(h / 24)}d`;
}

function fmtDuration(totalSeconds: number): string {
  const s = Math.round(totalSeconds);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function ClientSchedule() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const toast = useToast();

  const { data, isLoading } = useQuery({
    queryKey: ["admin-schedule", clientId],
    queryFn: () => scheduleApi.get(clientId!),
    refetchInterval: 30_000,
  });

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const { data: firesData } = useQuery({
    queryKey: ["admin-schedule-fires", clientId, "7d"],
    queryFn: () => scheduleApi.fires(clientId!, "7d"),
    enabled: !!clientId,
    refetchInterval: 30_000,
  });

  const clientTz = client?.timezone ?? "UTC";

  const [form, setForm] = useState<ScheduleConfig>({
    schedule_enabled: false, schedule_cadence: "daily",
    schedule_hour: 2, schedule_minute: 0, schedule_day_of_week: null,
  });
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data) return;
    setForm({
      schedule_enabled: data.schedule_enabled, schedule_cadence: data.schedule_cadence,
      schedule_hour: data.schedule_hour, schedule_minute: data.schedule_minute,
      schedule_day_of_week: data.schedule_day_of_week,
    });
    setDirty(false);
  }, [data]);

  function update<K extends keyof ScheduleConfig>(key: K, value: ScheduleConfig[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  }

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-schedule", clientId] });

  const saveMut = useMutation({
    mutationFn: () => scheduleApi.update(clientId!, form),
    onSuccess: () => { invalidate(); setDirty(false); toast("Schedule saved"); },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save schedule", "err"),
  });

  const pauseMut = useMutation({
    mutationFn: () => scheduleApi.pause(clientId!),
    onSuccess: () => { invalidate(); toast("Schedule paused"); },
  });

  const resumeMut = useMutation({
    mutationFn: () => scheduleApi.resume(clientId!),
    onSuccess: () => { invalidate(); toast("Schedule resumed"); },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to resume", "err"),
  });

  if (isLoading) return <EmptyState>Loading schedule...</EmptyState>;

  const isEnabled = data?.schedule_enabled ?? false;
  const isManual = data?.schedule_cadence === "manual";
  const nextRunAt = data?.next_scheduled_run_at ?? null;

  const fires = (firesData?.fires ?? []).slice().reverse(); // newest first
  const completedFires = fires.filter((f) => f.status === "completed");
  const avgSeconds = completedFires.length
    ? completedFires.reduce((sum, f) => sum + f.duration_seconds, 0) / completedFires.length
    : null;

  const cadenceSentence =
    form.schedule_cadence === "manual"
      ? "Runs only when triggered manually."
      : form.schedule_cadence === "hourly"
        ? `Runs every hour at :${String(form.schedule_minute).padStart(2, "0")} in ${clientTz}.`
        : form.schedule_cadence === "weekly"
          ? `Runs every ${DAYS[form.schedule_day_of_week ?? 0]} at ${timeLabel(form.schedule_hour, form.schedule_minute)} in ${clientTz}.`
          : `Runs daily at ${timeLabel(form.schedule_hour, form.schedule_minute)} in ${clientTz}.`;

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="lbl">Cadence</div>
          <div className="val" style={{ fontSize: 20 }}>{data?.schedule_cadence ?? "-"}</div>
          <div className="hint">
            {isManual
              ? "runs only when triggered"
              : `at ${timeLabel(data?.schedule_hour ?? 0, data?.schedule_minute ?? 0)} in ${clientTz}`}
          </div>
        </div>
        <div className="card">
          <div className="lbl">State</div>
          <div className="val" style={{ fontSize: 20, color: isEnabled ? "var(--good)" : "var(--warn)" }}>
            {isEnabled ? "Active" : isManual ? "Manual" : "Paused"}
          </div>
          <div className="hint">{isEnabled ? "runs fire on cadence" : "no automatic runs"}</div>
        </div>
        <div className="card">
          <div className="lbl">Next run</div>
          <div className="val" style={{ fontSize: 20 }}>{isEnabled ? timeUntil(nextRunAt) : "-"}</div>
          <div className="hint">{clientTz}</div>
        </div>
        <div className="card">
          <div className="lbl">Avg run length</div>
          <div className="val" style={{ fontSize: 20 }}>{avgSeconds != null ? fmtDuration(avgSeconds) : "-"}</div>
          <div className="hint">
            {completedFires.length ? `last ${completedFires.length} completed fires` : "no completed fires yet"}
          </div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph"><h3>Configuration</h3></div>

          <div className="fld">
            <label>Enabled</label>
            <TSwitch
              on={form.schedule_enabled}
              onToggle={() => update("schedule_enabled", !form.schedule_enabled)}
              disabled={form.schedule_cadence === "manual"}
              label="Enable automated runs"
            />
          </div>

          <div className="fld">
            <label>Cadence</label>
            <PillRow
              value={form.schedule_cadence}
              onChange={(c) => {
                update("schedule_cadence", c as ScheduleCadence);
                if (c === "manual") update("schedule_enabled", false);
              }}
              options={(["hourly", "daily", "weekly", "manual"] as const).map((c) => ({ value: c, label: c }))}
            />
          </div>

          {form.schedule_cadence !== "manual" && (
            <div style={{ display: "flex", gap: 10 }}>
              {form.schedule_cadence !== "hourly" && (
                <div className="fld" style={{ flex: 1 }}>
                  <label>Hour (UTC)</label>
                  <select value={form.schedule_hour} onChange={(e) => update("schedule_hour", Number(e.target.value))}>
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>{String(i).padStart(2, "0")}</option>
                    ))}
                  </select>
                </div>
              )}
              <div className="fld" style={{ flex: 1 }}>
                <label>Minute</label>
                <select value={form.schedule_minute} onChange={(e) => update("schedule_minute", Number(e.target.value))}>
                  {[0, 15, 30, 45].map((m) => <option key={m} value={m}>{String(m).padStart(2, "0")}</option>)}
                </select>
              </div>
              {form.schedule_cadence === "weekly" && (
                <div className="fld" style={{ flex: 1 }}>
                  <label>Day of week</label>
                  <select
                    value={form.schedule_day_of_week ?? 0}
                    onChange={(e) => update("schedule_day_of_week", Number(e.target.value))}
                  >
                    {DAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                </div>
              )}
            </div>
          )}

          <div className="fld"><div className="fh" style={{ marginBottom: 4 }}>{cadenceSentence}</div></div>

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn pri" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
              {saveMut.isPending ? "Saving..." : "Save changes"}
            </button>
            {isEnabled ? (
              <button className="btn danger" disabled={pauseMut.isPending} onClick={() => pauseMut.mutate()}>
                {pauseMut.isPending ? "Pausing..." : "Pause schedule"}
              </button>
            ) : !isManual ? (
              <button className="btn" disabled={resumeMut.isPending} onClick={() => resumeMut.mutate()}>
                {resumeMut.isPending ? "Resuming..." : "Resume"}
              </button>
            ) : null}
          </div>
        </div>

        <div className="panel">
          <div className="ph">
            <h3>Recent fires</h3>
            <span className="note">last {Math.min(fires.length, 6)}</span>
          </div>
          {fires.length === 0 ? (
            <EmptyState>No fires in the last 7 days.</EmptyState>
          ) : (
            <table className="tb">
              <thead>
                <tr><th>Fired</th><th>Status</th><th className="right">Duration</th><th>Run</th></tr>
              </thead>
              <tbody>
                {fires.slice(0, 6).map((f) => (
                  <tr key={f.id}>
                    <td className="dim2">{relTimePast(f.timestamp)}</td>
                    <td><RunStatusChip status={f.status} /></td>
                    <td className="right mono">{fmtDuration(f.duration_seconds)}</td>
                    <td>
                      <Link
                        to={`/clients/${clientId}/runs/${f.id}`}
                        style={{ textDecoration: "underline", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 2 }}
                      >
                        open <ArrowForwardRoundedIcon style={{ fontSize: 11 }} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {data && data.recent_runs.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)" }}>
            <h3 style={{ fontSize: 13.5, fontWeight: 650 }}>Scheduler activity</h3>
            <span style={{ color: "var(--ink4)", fontSize: 11.5 }}>what the scheduler enqueued for this client</span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr><th>Triggered</th><th>Status</th><th>Cadence</th><th className="right">Retries</th><th>Pipeline run</th></tr>
              </thead>
              <tbody>
                {data.recent_runs.map((r) => (
                  <tr key={r.id}>
                    <td className="dim2">{relTimePast(r.triggered_at)}</td>
                    <td>
                      <Chip tone={r.status === "completed" ? "good" : r.status === "failed" ? "bad" : r.status === "enqueued" ? "warn" : ""}>
                        {r.status}
                      </Chip>
                    </td>
                    <td className="dim2" style={{ textTransform: "capitalize" }}>{r.cadence}</td>
                    <td className="right mono">{r.retry_count}</td>
                    <td>
                      {r.run_id ? (
                        <Link
                          to={`/clients/${clientId}/runs/${r.run_id}`}
                          style={{ textDecoration: "underline", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 2 }}
                        >
                          open <ArrowForwardRoundedIcon style={{ fontSize: 11 }} />
                        </Link>
                      ) : (
                        <span className="dim">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
