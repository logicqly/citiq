import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import TrendingUpRoundedIcon from "@mui/icons-material/TrendingUpRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from "recharts";
import type { SvgIconComponent } from "@mui/icons-material";
import { clientsApi, recommendationsApi } from "../../api/client";
import type { RecommendationStatus } from "../../types";
import { TYPE_META, TYPE_ORDER, TypeBadge } from "./typeMeta";

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<RecommendationStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  revision_requested: "Revision",
  implemented: "Implemented",
};

const STATUS_COLORS: Record<RecommendationStatus, string> = {
  pending:            "bg-blue-50 text-blue-700 border-blue-200",
  approved:           "bg-emerald-50 text-emerald-700 border-emerald-200",
  rejected:           "bg-red-50 text-red-700 border-red-200",
  revision_requested: "bg-orange-50 text-orange-700 border-orange-200",
  implemented:        "bg-purple-50 text-purple-700 border-purple-200",
};

const PLATFORM_BADGE: Record<string, string> = {
  gemini:     "bg-amber-100 text-amber-800",
  perplexity: "bg-blue-100 text-blue-800",
  openai:     "bg-emerald-100 text-emerald-800",
  anthropic:  "bg-purple-100 text-purple-800",
};

// ── Avatar helpers (client chips) ─────────────────────────────────────────────

const AVATAR_COLORS = [
  "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#6366f1",
];
function avatarColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

// ── Mock chart data ───────────────────────────────────────────────────────────

const QUEUE_DAYS = ["M","T","W","T","F","S","S","M","T","W","T","F","S","S"];
const QUEUE_DATA = QUEUE_DAYS.map((day, i) => ({
  day,
  briefs:   [8, 5, 6, 4, 9, 3, 2, 11, 7, 13, 14, 15, 4, 3][i],
  approved: [2, 1, 0, 2, 1, 0, 0, 0,  0, 0,  0,  0,  1, 0][i],
}));

// ── Small helper components ───────────────────────────────────────────────────

function StatusBadge({ status }: { status: RecommendationStatus }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[status]}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" />
      {STATUS_LABELS[status]}
    </span>
  );
}

function PlatformBadge({ platform }: { platform: string | null }) {
  if (!platform) return <span className="text-gray-400 text-xs">-</span>;
  const cls = PLATFORM_BADGE[platform.toLowerCase()] ?? "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {platform.charAt(0).toUpperCase() + platform.slice(1)}
    </span>
  );
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// ── Classification by type (donut + clickable legend) ─────────────────────────

function TypeMix({ byType, total, activeType, onSelect }: {
  byType: Record<string, number>;
  total: number;
  activeType: string;
  onSelect: (type: string) => void;
}) {
  const slices = TYPE_ORDER
    .map((t) => ({ type: t as string, label: TYPE_META[t].label, hex: TYPE_META[t].hex, value: byType[t] ?? 0 }))
    .filter((s) => s.value > 0);

  if (total === 0 || slices.length === 0) {
    return (
      <div className="h-40 flex items-center justify-center text-sm text-gray-400">
        No recommendations yet
      </div>
    );
  }

  return (
    <div className="flex flex-col sm:flex-row items-center gap-5">
      <div className="relative shrink-0" style={{ width: 160, height: 160 }}>
        <PieChart width={160} height={160}>
          <Pie data={slices} cx={76} cy={76} innerRadius={54} outerRadius={74}
            dataKey="value" nameKey="label" startAngle={90} endAngle={-270}
            paddingAngle={2} cornerRadius={2} strokeWidth={0}>
            {slices.map((s) => <Cell key={s.type} fill={s.hex} />)}
          </Pie>
          <Tooltip
            contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb", boxShadow: "0 4px 12px rgba(0,0,0,0.06)" }}
            formatter={(v: number, name: string) => [`${v} (${Math.round((v / total) * 100)}%)`, name]}
          />
        </PieChart>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-2xl font-bold text-gray-900">{total}</span>
          <span className="text-xs text-gray-400">total</span>
        </div>
      </div>
      <div className="flex-1 w-full min-w-0 space-y-0.5">
        {slices.map((s) => (
          <button
            key={s.type}
            onClick={() => onSelect(activeType === s.type ? "" : s.type)}
            title={activeType === s.type ? "Clear type filter" : `Show only ${s.label}`}
            className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors ${
              activeType === s.type ? "bg-gray-100" : "hover:bg-gray-50"
            }`}
          >
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: s.hex }} />
            <span className="text-xs text-gray-600 truncate flex-1">{s.label}</span>
            <span className="text-xs font-semibold text-gray-900">{s.value}</span>
            <span className="text-[11px] text-gray-400 w-8 text-right">{Math.round((s.value / total) * 100)}%</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Type tab bar (classification filter with counts) ──────────────────────────

function TypeTabBar({ byType, total, value, onChange }: {
  byType: Record<string, number>;
  total: number;
  value: string;
  onChange: (v: string) => void;
}) {
  const tabs: { value: string; label: string; count: number; Icon: SvgIconComponent | null }[] = [
    { value: "", label: "All types", count: total, Icon: null },
    ...TYPE_ORDER.map((t) => ({
      value: t as string,
      label: TYPE_META[t].label,
      count: byType[t] ?? 0,
      Icon: TYPE_META[t].Icon as SvgIconComponent | null,
    })),
  ];
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto pb-1 -mb-1">
      {tabs.map((tab) => {
        const selected = value === tab.value;
        return (
          <button
            key={tab.value}
            onClick={() => onChange(tab.value)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
              selected
                ? "bg-gray-900 border-gray-900 text-white"
                : "bg-white border-gray-200 text-gray-600 hover:bg-gray-50"
            }`}
          >
            {tab.Icon && <tab.Icon style={{ fontSize: 14 }} />}
            {tab.label}
            <span className="text-gray-400">{tab.count}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ dot, label, value, sub, trend }: {
  dot: string; label: string; value: string | number; sub?: string; trend?: string;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="flex items-center gap-1.5 mb-2">
        <span className={`w-2 h-2 rounded-full ${dot}`} />
        <p className="text-xs text-gray-500 font-medium">{label}</p>
      </div>
      <p className="text-3xl font-bold text-gray-900">{value}</p>
      {trend && (
        <p className="inline-flex items-center gap-0.5 text-xs text-emerald-600 mt-1">
          <TrendingUpRoundedIcon style={{ fontSize: 13 }} />{trend}
        </p>
      )}
      {sub && !trend && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

// ── Filter pill group ─────────────────────────────────────────────────────────

function PillGroup({ options, value, onChange }: {
  options: { label: string; value: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-0 border border-gray-200 rounded-lg overflow-hidden bg-white">
      {options.map((opt, i) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
            i > 0 ? "border-l border-gray-200" : ""
          } ${
            value === opt.value
              ? "bg-gray-900 text-white"
              : "text-gray-600 hover:bg-gray-50"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function RecommendationList() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const clientId     = searchParams.get("client_id") ?? "";
  const statusFilter = searchParams.get("status")    ?? "pending";
  const typeFilter   = searchParams.get("type")      ?? "";
  const priorityFilter = searchParams.get("priority") ?? "";
  const page = parseInt(searchParams.get("page") ?? "1", 10);

  // ── BUG FIX: don't reset page when the key being set IS "page" ──
  const setFilter = (key: string, val: string) => {
    const next = new URLSearchParams(searchParams);
    if (val) next.set(key, val); else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setSearchParams(next);
  };

  const { data: clients } = useQuery({
    queryKey: ["admin-clients"],
    queryFn: () => clientsApi.list("active"),
  });

  const { data: summary } = useQuery({
    queryKey: ["rec-summary", clientId],
    queryFn: () => recommendationsApi.summary(clientId),
    enabled: !!clientId,
  });

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["recommendations", clientId, statusFilter, typeFilter, priorityFilter, page],
    queryFn: () =>
      recommendationsApi.list(clientId, {
        status:   statusFilter || undefined,
        type:     typeFilter   || undefined,
        priority: priorityFilter || undefined,
        page,
        per_page: 20,
      }),
    enabled: !!clientId,
  });

  const totalPages = data ? Math.ceil(data.total / 20) : 1;
  const selectedClient = clients?.find((c) => c.id === clientId);
  const pending     = summary?.by_status?.pending     ?? 0;
  const approved    = summary?.by_status?.approved    ?? 0;
  const rejected    = summary?.by_status?.rejected    ?? 0;
  const implemented = summary?.by_status?.implemented ?? 0;

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Recommendations</h1>
          <p className="text-xs text-gray-400 mt-0.5">Engine-generated briefs across all clients</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 font-medium hover:bg-gray-50 transition-colors">
            Export
          </button>
        </div>
      </div>

      {/* ── Client selector bar ── */}
      <div className="bg-white border border-gray-200 rounded-xl px-4 py-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Client</span>
          {/* Client chips */}
          <div className="flex items-center gap-2 flex-wrap flex-1">
            {clients?.map((c) => {
              const color = avatarColor(c.name);
              const isSelected = c.id === clientId;
              return (
                <button
                  key={c.id}
                  onClick={() => {
                    const next = new URLSearchParams();
                    next.set("client_id", c.id);
                    next.set("status", "pending");
                    setSearchParams(next);
                  }}
                  style={isSelected ? { background: color + "18", borderColor: color } : undefined}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                    isSelected
                      ? "border-current"
                      : "border-gray-200 text-gray-600 hover:border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ background: color }}
                  />
                  <span style={isSelected ? { color } : undefined}>{c.slug}</span>
                </button>
              );
            })}
          </div>
          {/* Stats inline */}
          {selectedClient && summary && (
            <p className="text-xs text-gray-400 shrink-0">
              <span className="font-medium text-gray-700">{pending} pending</span>
              {", "}
              {approved} approved
              {summary.last_generated_at && (
                <>, last engine cycle {Math.floor((Date.now() - new Date(summary.last_generated_at).getTime()) / 3600000)}h ago</>
              )}
            </p>
          )}
          {/* All clients / Per client toggle */}
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5 shrink-0 ml-auto">
            {["All clients", "Per client"].map((label) => (
              <button
                key={label}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  label === "Per client"
                    ? "bg-white text-gray-900 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {!clientId && (
        <div className="bg-white border border-gray-200 rounded-xl p-10 text-center">
          <p className="text-sm text-gray-400">Select a client above to view recommendations.</p>
        </div>
      )}

      {clientId && (
        <>
          {/* ── Stat cards ── */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
            <StatCard
              dot="bg-blue-500" label="Pending" value={pending}
              trend={`${summary?.pending_high_priority ?? 12} since yesterday`}
            />
            <StatCard
              dot="bg-emerald-500" label="Approved" value={approved}
              sub={approved === 0 ? "queue clear, ready" : `${approved} approved`}
            />
            <StatCard
              dot="bg-rose-400" label="Rejected" value={rejected}
              sub={rejected === 0 ? "0% reject rate" : `${Math.round((rejected / Math.max(pending + approved + rejected, 1)) * 100)}% reject rate`}
            />
            <StatCard
              dot="bg-amber-400" label="Implemented" value={implemented}
              sub="awaiting client review"
            />
          </div>

          {/* ── Charts row ── */}
          <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4">
            {/* Recommendation queue bar chart */}
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <p className="text-sm font-semibold text-gray-900">Recommendation queue (last 14 days)</p>
              <p className="text-xs text-gray-400 mb-4">New briefs vs approvals per day</p>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={QUEUE_DATA} margin={{ top: 4, right: 4, left: -28, bottom: 0 }} barSize={18}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#9ca3af" }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: "#9ca3af" }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
                  />
                  <Bar dataKey="briefs"   name="New briefs" stackId="a" fill="#3b82f6" radius={[0, 0, 0, 0]} />
                  <Bar dataKey="approved" name="Approved"   stackId="a" fill="#bfdbfe" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              {/* Legend */}
              <div className="flex items-center gap-4 mt-3">
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <span className="w-2.5 h-2.5 rounded-sm bg-blue-500 inline-block" />New briefs
                </div>
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <span className="w-2.5 h-2.5 rounded-sm bg-blue-200 inline-block" />Approved
                </div>
              </div>
            </div>

            {/* Classification by type donut */}
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <p className="text-sm font-semibold text-gray-900">Classification by type</p>
              <p className="text-xs text-gray-400 mb-4">
                {selectedClient ? `All ${selectedClient.name} recommendations` : "All recommendations"}, every status. Click a row to filter.
              </p>
              {summary ? (
                <TypeMix
                  byType={summary.by_type ?? {}}
                  total={summary.total ?? 0}
                  activeType={typeFilter}
                  onSelect={(v) => setFilter("type", v)}
                />
              ) : (
                <div className="h-40 rounded-lg bg-gray-100 animate-pulse" />
              )}
            </div>
          </div>

          {/* ── Type classification tabs ── */}
          <TypeTabBar
            byType={summary?.by_type ?? {}}
            total={summary?.total ?? 0}
            value={typeFilter}
            onChange={(v) => setFilter("type", v)}
          />

          {/* ── Priority filter + result count ── */}
          <div className="flex items-center gap-3 flex-wrap">
            <PillGroup
              value={priorityFilter || ""}
              onChange={(v) => setFilter("priority", v)}
              options={[
                { label: "All priorities", value: "" },
                { label: "High",           value: "high" },
                { label: "Med",            value: "medium" },
                { label: "Low",            value: "low" },
              ]}
            />
            <span className="ml-auto text-xs text-gray-400 font-medium">
              Showing {data?.items.length ?? 0} of {data?.total ?? 0}
            </span>
          </div>

          {/* ── Table ── */}
          {isLoading ? (
            <div className="space-y-2">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="h-14 bg-gray-100 rounded-xl animate-pulse" />
              ))}
            </div>
          ) : data?.items.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-xl p-10 text-center">
              <p className="text-sm text-gray-400">No recommendations found for these filters.</p>
            </div>
          ) : (
            <div className={`bg-white border border-gray-200 rounded-xl overflow-hidden transition-opacity ${isFetching ? "opacity-70" : ""}`}>
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100 bg-gray-50">
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">Type</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">Title</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider hidden md:table-cell">Platform</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">Status</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider hidden lg:table-cell">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {data?.items.map((rec) => (
                      <tr
                        key={rec.id}
                        onClick={() => navigate(`/recommendations/${rec.id}?client_id=${clientId}`)}
                        className="hover:bg-gray-50 cursor-pointer transition-colors"
                      >
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <TypeBadge type={rec.type} />
                        </td>
                        <td className="px-4 py-3.5 max-w-xs">
                          <p className="text-gray-900 font-medium truncate">{rec.title}</p>
                          {rec.target_query && (
                            <p className="text-xs text-gray-400 truncate mt-0.5">{rec.target_query}</p>
                          )}
                        </td>
                        <td className="px-4 py-3.5 hidden md:table-cell whitespace-nowrap">
                          <PlatformBadge platform={rec.platform} />
                        </td>
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <StatusBadge status={rec.status} />
                        </td>
                        <td className="px-4 py-3.5 hidden lg:table-cell whitespace-nowrap">
                          <span className="text-gray-400 text-xs">{fmtDate(rec.created_at)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* ── Mobile card list ── */}
              <div className="sm:hidden divide-y divide-gray-100">
                {data?.items.map((rec) => (
                  <div
                    key={rec.id}
                    onClick={() => navigate(`/recommendations/${rec.id}?client_id=${clientId}`)}
                    className="px-4 py-3 space-y-1.5 cursor-pointer hover:bg-gray-50 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <TypeBadge type={rec.type} />
                      <StatusBadge status={rec.status} />
                    </div>
                    <p className="text-sm text-gray-900 font-medium">{rec.title}</p>
                    {rec.target_query && (
                      <p className="text-xs text-gray-400 truncate">{rec.target_query}</p>
                    )}
                    <div className="flex items-center gap-3 text-xs text-gray-400">
                      <PlatformBadge platform={rec.platform} />
                      <span className="ml-auto">{fmtDate(rec.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>

              {/* ── Pagination ── */}
              {totalPages > 1 && (
                <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                  <p className="text-xs text-gray-400">Page {page} of {totalPages}</p>
                  <div className="flex gap-2">
                    <button
                      disabled={page <= 1}
                      onClick={() => setFilter("page", String(page - 1))}
                      className="inline-flex items-center gap-0.5 px-3 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-600 font-medium
                        disabled:opacity-40 hover:bg-gray-50 transition-colors"
                    >
                      <ChevronLeftRoundedIcon style={{ fontSize: 16 }} /> Prev
                    </button>
                    <button
                      disabled={page >= totalPages}
                      onClick={() => setFilter("page", String(page + 1))}
                      className="inline-flex items-center gap-0.5 px-3 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-600 font-medium
                        disabled:opacity-40 hover:bg-gray-50 transition-colors"
                    >
                      Next <ChevronRightRoundedIcon style={{ fontSize: 16 }} />
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
