import { Link } from "react-router-dom";
import type { RecommendationListItem } from "../../types";
import { LifeChip, PlatformCell, PriorityTag, TypeTag, relTime, usdFmt } from "../ui/ui";

const PRIORITY_SHORT: Record<string, string> = { high: "HIGH", medium: "MED", low: "LOW" };

// Impact-ranked card. The engine ranks by priority (high, medium, low); the
// left slot surfaces that rank so nobody reads 400 briefs to find the one
// that matters.
//
// `clientPath` is the current page's client route segment (its slug, once
// the client detail is loaded) — passed by every caller so the "from run"
// link matches the slug-based URLs the rest of the app uses. Falls back to
// rec.client_id (a UUID) so the card still works if ever rendered outside a
// client-scoped page.
export function RecCard({ rec, onOpen, clientPath }: {
  rec: RecommendationListItem; onOpen: (rec: RecommendationListItem) => void; clientPath?: string;
}) {
  return (
    <div className="rec" onClick={() => onOpen(rec)}>
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
          <PlatformCell platform={rec.platform} />
        </div>
        <h4>{rec.title}</h4>
        {rec.target_query && <div className="q">"{rec.target_query}"</div>}
        <div className="meta">
          {relTime(rec.created_at)}
          {rec.run_id && (
            <>
              {" "}from{" "}
              <Link
                to={`/clients/${clientPath ?? rec.client_id}/runs/${rec.run_id}`}
                onClick={(e) => e.stopPropagation()}
                style={{ textDecoration: "underline" }}
              >
                {rec.run_display_id ?? rec.run_id.slice(0, 8)}
              </Link>
            </>
          )}
          {rec.generation_model && <>, {rec.generation_model}</>}
          {rec.generation_cost_usd != null && <>, {usdFmt(rec.generation_cost_usd, 2)}</>}
        </div>
      </div>
    </div>
  );
}
