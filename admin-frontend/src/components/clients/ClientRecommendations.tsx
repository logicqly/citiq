import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { recommendationsApi } from "../../api/client";
import type { RecommendationListItem, RecommendationType } from "../../types";
import { Drawer, EmptyState, PillRow, REC_TYPE_LABELS } from "../ui/ui";
import { RecCard } from "../recommendations/RecCard";
import { RecDetailBody } from "../recommendations/RecDrawer";

// Queue groups (16 Jul lifecycle): For review, In progress, Published, Archived.
const GROUP_STATUS: Record<string, string> = {
  pending: "pending,revision_requested",
  approved: "approved",
  implemented: "implemented",
  archived: "rejected",
};

const GROUP_ORDER = ["pending", "approved", "implemented", "archived"] as const;
type Group = (typeof GROUP_ORDER)[number];

const GROUP_LABEL: Record<Group, string> = {
  pending: "For review",
  approved: "In progress",
  implemented: "Published",
  archived: "Archived",
};

const PRIORITY_WEIGHT: Record<string, number> = { high: 0, medium: 1, low: 2 };

const TYPE_ORDER: RecommendationType[] = [
  "content_brief", "schema_markup", "llms_txt", "authority_building",
];

function RecDrawerLoader({ recId, onClose }: { recId: string; onClose: () => void }) {
  const { data: rec, isLoading } = useQuery({
    queryKey: ["recommendation", recId],
    queryFn: () => recommendationsApi.get(recId),
  });

  return (
    <Drawer onClose={onClose}>
      {isLoading ? (
        <EmptyState>Loading...</EmptyState>
      ) : rec ? (
        <RecDetailBody rec={rec} onClose={onClose} />
      ) : (
        <EmptyState>Recommendation not found.</EmptyState>
      )}
    </Drawer>
  );
}

export function ClientRecommendations() {
  const { clientId } = useParams<{ clientId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const tab = (searchParams.get("tab") as Group) ?? "pending";
  const typeFilter = searchParams.get("type") ?? "all";
  const openRecId = searchParams.get("rec");

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value == null || value === "" || (key === "tab" && value === "pending") || (key === "type" && value === "all")) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setSearchParams(next, { replace: true });
  };

  const { data: summary } = useQuery({
    queryKey: ["rec-summary", clientId],
    queryFn: () => recommendationsApi.summary(clientId!),
    enabled: !!clientId,
  });

  const { data: listData, isLoading } = useQuery({
    queryKey: ["client-recs", clientId, tab, typeFilter],
    queryFn: () =>
      recommendationsApi.list(clientId!, {
        status: GROUP_STATUS[tab],
        type: typeFilter !== "all" ? typeFilter : undefined,
        per_page: 100,
        sort_by: "created_at",
        sort_order: "desc",
      }),
    enabled: !!clientId,
  });

  const byStatus = summary?.by_status ?? {};
  const counts: Record<Group, number> = {
    pending: (byStatus.pending ?? 0) + (byStatus.revision_requested ?? 0),
    approved: byStatus.approved ?? 0,
    implemented: byStatus.implemented ?? 0,
    archived: byStatus.rejected ?? 0,
  };
  const total = summary?.total ?? 0;

  const items = useMemo(() => {
    const list = listData?.items ?? [];
    return [...list].sort(
      (a, b) =>
        (PRIORITY_WEIGHT[a.priority] ?? 3) - (PRIORITY_WEIGHT[b.priority] ?? 3) ||
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  }, [listData]);

  const presentTypes = TYPE_ORDER.filter((t) => (summary?.by_type?.[t] ?? 0) > 0);

  const openRec = (rec: RecommendationListItem) => setParam("rec", rec.id);
  const closeRec = () => setParam("rec", null);

  return (
    <>
      <div className="banner">
        <span className="bi dim"><InfoOutlinedIcon style={{ fontSize: 15 }} /></span>
        <div>
          <b>Ranked by impact, you should never read 400 briefs to find the one that matters.</b>
          <div className="note">
            Engine cycle every ~2 weeks, not every run. Lifecycle: For review, then In progress, then Published, then it leaves the queue.
            Counts reconcile: {total} total = {counts.pending}+{counts.approved}+{counts.implemented}+{counts.archived}.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <PillRow
          value={tab}
          onChange={(v) => setParam("tab", v)}
          options={GROUP_ORDER.map((g) => ({
            value: g,
            label: <>{GROUP_LABEL[g]} <span className="mono">{counts[g]}</span></>,
          }))}
        />
        <div style={{ flex: 1 }} />
        <PillRow
          value={typeFilter}
          onChange={(v) => setParam("type", v)}
          options={[
            { value: "all", label: "All types" },
            ...presentTypes.map((t) => ({ value: t as string, label: REC_TYPE_LABELS[t] })),
          ]}
        />
      </div>

      {isLoading ? (
        <EmptyState>Loading...</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>
          {tab === "pending"
            ? "Review queue is clear. The next engine cycle runs with the next scheduled run."
            : "Nothing here yet."}
        </EmptyState>
      ) : (
        items.map((rec) => <RecCard key={rec.id} rec={rec} onOpen={openRec} clientPath={clientId} />)
      )}

      {openRecId && <RecDrawerLoader recId={openRecId} onClose={closeRec} />}
    </>
  );
}
