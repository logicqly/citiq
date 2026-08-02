import { useMemo, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import { clientsApi, costApi } from "../../api/client";
import type { ClientSummary } from "../../types";
import { CreateClientModal } from "./CreateClientModal";
import { AreaChart } from "../ui/charts";
import {
  Chip, ClientStatusChip, EmptyState, PillRow, SearchBox, getInitials, pctFmt, relTime,
} from "../ui/ui";
import { usePendingReview } from "../ui/usePendingReview";

function timeLabel(hour: number, minute: number) {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function NextRunChip({ c }: { c: ClientSummary }) {
  if (c.schedule_enabled) {
    return (
      <Chip tone="good">
        {c.schedule_cadence} {timeLabel(c.schedule_hour, c.schedule_minute)} UTC
      </Chip>
    );
  }
  if (c.schedule_cadence === "manual") return <Chip>Manual</Chip>;
  return <Chip tone="warn">Paused</Chip>;
}

export function ClientList() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<"active" | "">("active");
  const [showCreate, setShowCreate] = useState(false);
  const [search, setSearch] = useState("");

  const { data: clients = [], isLoading } = useQuery({
    queryKey: ["admin-clients", statusFilter],
    queryFn: () => clientsApi.list(statusFilter),
  });

  // Global review-queue numbers (aggregated per client — the API is per-client).
  const { clients: allClients, byClient: pendingByClient, total: pendingTotal } = usePendingReview();

  // 30-day spend per client, summed for the KPI band.
  const runStats = useQueries({
    queries: allClients.map((c) => ({
      queryKey: ["admin-client-run-stats", c.id, "30d"],
      queryFn: () => costApi.getClientRunStats(c.id, "30d" as const),
      staleTime: 60_000,
    })),
  });
  const spend30 = runStats.reduce((s, q) => s + (q.data?.total_cost_usd ?? 0), 0);
  const runs30 = runStats.reduce((s, q) => s + (q.data?.run_count ?? 0), 0);

  const visibleClients = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (clients as ClientSummary[]).filter((c) =>
      !term ||
      c.name.toLowerCase().includes(term) ||
      c.slug.toLowerCase().includes(term) ||
      (c.industry ?? "").toLowerCase().includes(term)
    );
  }, [clients, search]);

  const activeCount = allClients.filter((c) => c.status === "active").length;
  const pausedCount = allClients.filter((c) => c.status === "paused").length;
  const citing = allClients.filter((c) => c.latest_citation_rate != null);
  const avgCitation = citing.length
    ? citing.reduce((s, c) => s + (c.latest_citation_rate ?? 0), 0) / citing.length
    : null;

  // Aggregate citation trend: for each of the last 8 run slots, average the
  // per-client citation history at that slot (oldest first).
  const trend = useMemo(() => {
    const histories = allClients
      .map((c) => (c.citation_history ?? []).slice(-8))
      .filter((h) => h.length >= 2);
    const maxLen = Math.max(0, ...histories.map((h) => h.length));
    const points: number[] = [];
    for (let i = 0; i < maxLen; i++) {
      const at = histories
        .map((h) => h[h.length - maxLen + i])
        .filter((v): v is number => v != null);
      if (at.length) points.push((at.reduce((s, v) => s + v, 0) / at.length) * 100);
    }
    return points;
  }, [allClients]);

  return (
    <>
      <div className="phead">
        <div className="grow">
          <h1 className="page">Clients</h1>
          <div className="sub">{allClients.length} clients, every number below is derived from run data</div>
        </div>
        <PillRow
          value={statusFilter}
          onChange={setStatusFilter}
          options={[{ value: "active", label: "Active" }, { value: "", label: "All" }]}
        />
        <button className="btn pri" onClick={() => setShowCreate(true)}>
          <AddRoundedIcon style={{ fontSize: 15 }} /> New client
        </button>
      </div>

      <div className="cards">
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--good)" }} />Active clients</div>
          <div className="val">{activeCount}/{allClients.length}</div>
          <div className="hint">{pausedCount} paused</div>
        </div>
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--warn)" }} />Pending review</div>
          <div className="val">{pendingTotal}</div>
          <div className="hint">recommendations awaiting a human</div>
        </div>
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--white)" }} />30-day spend</div>
          <div className="val">${spend30.toFixed(0)}</div>
          <div className="hint">
            {runs30 > 0 ? `across ${runs30} runs, ~$${(spend30 / runs30).toFixed(0)}/run` : "no runs in the window"}
          </div>
        </div>
        <div className="card">
          <div className="lbl"><span className="pd" style={{ background: "var(--ink3)" }} />Avg citation rate</div>
          <div className="val">{pctFmt(avgCitation)}</div>
          <div className="hint">latest run per client, hollow excluded</div>
        </div>
      </div>

      <div className="panel">
        <div className="ph">
          <h3>Citation trend</h3>
          <span className="note">latest-run citation per client, last 8 runs</span>
          <div className="sp" />
        </div>
        {trend.length > 1 ? (
          <>
            <AreaChart vals={trend} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--ink5)", fontFamily: "var(--mono)", marginTop: 6 }}>
              <span>{trend.length} runs ago</span>
              <span>latest</span>
            </div>
          </>
        ) : (
          <EmptyState>Not enough completed runs yet.</EmptyState>
        )}
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <div style={{ display: "flex", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)" }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search clients..." style={{ flex: 1 }} />
        </div>
        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : visibleClients.length === 0 ? (
          <EmptyState>{search ? "No clients match your search." : "No clients found."}</EmptyState>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th>Client</th>
                  <th>Status</th>
                  <th>Industry</th>
                  <th className="right">Prompts</th>
                  <th>Last run</th>
                  <th>Next run</th>
                  <th className="right">Citation</th>
                  <th className="right">Needs review</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleClients.map((c) => {
                  const pn = pendingByClient.get(c.id) ?? 0;
                  return (
                    <tr key={c.id} className="rowlink" onClick={() => navigate(`/clients/${c.slug}/overview`)}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                          <div className="av">{getInitials(c.name)}</div>
                          <div>
                            <b style={{ fontSize: 13.5 }}>{c.name}</b>
                            <div className="mono dim" style={{ fontSize: 11 }}>{c.slug}</div>
                          </div>
                        </div>
                      </td>
                      <td><ClientStatusChip status={c.status} /></td>
                      <td className="dim2" style={{ maxWidth: 220 }}>{c.industry ?? "-"}</td>
                      <td className="right mono">{c.total_prompts}</td>
                      <td className="dim2">{c.last_run_at ? relTime(c.last_run_at) : "-"}</td>
                      <td><NextRunChip c={c} /></td>
                      <td className="right"><span className="mono" style={{ fontSize: 13 }}>{pctFmt(c.latest_citation_rate)}</span></td>
                      <td className="right">{pn > 0 ? <Chip tone="warn">{pn}</Chip> : <span className="dim">-</span>}</td>
                      <td className="dim"><ChevronRightRoundedIcon style={{ fontSize: 14 }} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="footer-note">
        No dummy metrics on this screen: visitors/peak-hours widgets from the old console were removed, every value here is computable from the API today.
      </div>

      {showCreate && (
        <CreateClientModal
          onClose={() => setShowCreate(false)}
          onCreated={(client) => {
            qc.invalidateQueries({ queryKey: ["admin-clients"] });
            setShowCreate(false);
            navigate(`/clients/${client.slug}/overview`);
          }}
        />
      )}
    </>
  );
}
