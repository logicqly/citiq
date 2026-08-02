import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { useParams, useNavigate } from "react-router-dom";
import { recommendations } from "../lib/api";
import type { ClientRecommendationDetail, ClientRecommendationListItem } from "../lib/api";
import { EmptyState, LIFE_CLIENT, LifeChip, PriorityTag, TypeTag, relTime } from "./ui";

const PRIORITY_WEIGHT: Record<string, number> = { high: 0, medium: 1, low: 2 };
const PRIORITY_SHORT: Record<string, string> = { high: "HIGH", medium: "MED", low: "LOW" };

function prettyKey(key: string): string {
  const label = key.replace(/_/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function ContentSection({ content }: { content: Record<string, unknown> }) {
  return (
    <>
      {Object.entries(content).map(([key, value]) => (
        <div key={key} className="dsec">
          <div className="dl">{prettyKey(key)}</div>
          {Array.isArray(value) ? (
            <ul>
              {value.map((item, i) => (
                <li key={i}>{typeof item === "object" && item !== null ? JSON.stringify(item) : String(item)}</li>
              ))}
            </ul>
          ) : typeof value === "object" && value !== null ? (
            <pre>{JSON.stringify(value, null, 2)}</pre>
          ) : (
            <p style={{ whiteSpace: "pre-wrap" }}>{String(value)}</p>
          )}
        </div>
      ))}
    </>
  );
}

function RecDrawer({ recId, onClose }: { recId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery<ClientRecommendationDetail>({
    queryKey: ["client-rec", recId],
    queryFn: () => recommendations.get(recId),
  });

  return (
    <div className="drawer-wrap">
      <div className="scrim" onClick={onClose} />
      <div className="drawer">
        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : data ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <LifeChip status={data.status} />
              <PriorityTag priority={data.priority} />
              <TypeTag type={data.type} />
              <button
                style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0 }}
                onClick={onClose}
                aria-label="Close"
              >
                <CloseRoundedIcon style={{ fontSize: 17 }} />
              </button>
            </div>

            <h2>{data.title}</h2>

            {data.target_query && (
              <div className="dsec">
                <div className="dl">Target query</div>
                <p style={{ fontStyle: "italic" }}>"{data.target_query}"</p>
              </div>
            )}

            <ContentSection content={data.content} />

            {data.history.length > 0 && (
              <div className="dsec">
                <div className="dl">Progress</div>
                <div className="hist">
                  {data.history.map((h) => (
                    <div key={h.id} className="h">
                      <b style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        {h.old_status && (
                          <>
                            {LIFE_CLIENT[h.old_status]?.label ?? h.old_status}
                            <ArrowForwardRoundedIcon style={{ fontSize: 11 }} />
                          </>
                        )}
                        {LIFE_CLIENT[h.new_status]?.label ?? h.new_status}
                      </b>{" "}
                      <span className="dim2">by {h.actor === "engine" ? "Engine" : "Origo"}</span>
                      <div className="w">{relTime(h.created_at)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="dsec">
              <p className="dim" style={{ fontSize: 12 }}>
                Questions about this recommendation? Your Origo account manager reviews the queue with you every week.
              </p>
            </div>
          </>
        ) : (
          <EmptyState>Recommendation not found.</EmptyState>
        )}
      </div>
    </div>
  );
}

export function RecommendationsPage() {
  const { recId } = useParams<{ recId?: string }>();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");

  const { data: summary } = useQuery({
    queryKey: ["client-rec-summary"],
    queryFn: () => recommendations.getSummary(),
  });

  const { data, isLoading } = useQuery({
    queryKey: ["client-recs", page, statusFilter],
    queryFn: () => recommendations.list({ page, status: statusFilter || undefined }),
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1;
  const pending = summary?.by_status?.pending ?? 0;

  const items = [...(data?.items ?? [])].sort(
    (a, b) =>
      (PRIORITY_WEIGHT[a.priority] ?? 3) - (PRIORITY_WEIGHT[b.priority] ?? 3) ||
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  return (
    <>
      {recId && <RecDrawer recId={recId} onClose={() => navigate("/dashboard/recommendations")} />}

      <div className="phead">
        <div className="grow">
          <h1 className="page">Recommendations</h1>
          <div className="sub">
            {summary?.total ?? 0} in the current cycle, impact-ranked, every item is human-reviewed by the Origo team before work starts
          </div>
        </div>
      </div>

      {pending > 0 && (
        <div className="banner">
          <span className="bi" style={{ color: "var(--ink3)" }}><InfoOutlinedIcon style={{ fontSize: 15 }} /></span>
          <div>
            <b>{pending} recommendation{pending > 1 ? "s" : ""} in review at Origo</b>
            <div className="note">
              Our team approves, refines or rejects engine output before it reaches production, nothing is published without a human sign-off.
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
        <div className="pillrow">
          {[
            { value: "", label: "All" },
            { value: "pending", label: "In review" },
            { value: "approved", label: "In production" },
            { value: "implemented", label: "Published" },
          ].map((o) => (
            <button
              key={o.value}
              className={`pi${statusFilter === o.value ? " on" : ""}`}
              onClick={() => { setStatusFilter(o.value); setPage(1); }}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <EmptyState>Loading...</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>No recommendations in this state.</EmptyState>
      ) : (
        items.map((rec: ClientRecommendationListItem) => (
          <div key={rec.id} className="rec" onClick={() => navigate(`/dashboard/recommendations/${rec.id}`)}>
            <div className="imp">
              <div
                className="n"
                style={{ fontSize: 17, color: rec.priority === "high" ? "var(--bad)" : rec.priority === "medium" ? "var(--warn)" : "var(--ink2)" }}
              >
                {PRIORITY_SHORT[rec.priority] ?? rec.priority}
              </div>
              <div className="t">Priority</div>
            </div>
            <div className="bd">
              <div className="rw">
                <LifeChip status={rec.status} />
                <PriorityTag priority={rec.priority} />
                <TypeTag type={rec.type} />
              </div>
              <h4>{rec.title}</h4>
              {rec.target_query && <div className="q">"{rec.target_query}"</div>}
              <div className="meta">{relTime(rec.created_at)}</div>
            </div>
          </div>
        ))
      )}

      {totalPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 14 }}>
          <button className="btn sm" disabled={page === 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeftRoundedIcon style={{ fontSize: 14 }} /> Prev
          </button>
          <span className="mono dim" style={{ fontSize: 11 }}>Page {page} of {totalPages}</span>
          <button className="btn sm" disabled={page === totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
            Next <ChevronRightRoundedIcon style={{ fontSize: 14 }} />
          </button>
        </div>
      )}
    </>
  );
}
