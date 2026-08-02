import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import { recommendationsApi } from "../../api/client";
import { EmptyState } from "../ui/ui";
import { RecDetailBody } from "./RecDrawer";

// Standalone page for deep links to a single recommendation. Day-to-day review
// happens in the drawer on each client's Recommendations tab.
export function RecommendationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const clientId = searchParams.get("client_id") ?? "";

  const { data: rec, isLoading } = useQuery({
    queryKey: ["recommendation", id],
    queryFn: () => recommendationsApi.get(id!),
    enabled: !!id,
  });

  const backTo = clientId || rec?.client_id
    ? `/clients/${clientId || rec?.client_id}/recommendations`
    : "/clients";

  return (
    <>
      <button
        className="btn sm"
        style={{ marginBottom: 18 }}
        onClick={() => navigate(backTo)}
      >
        <ArrowBackRoundedIcon style={{ fontSize: 13 }} /> Back to recommendations
      </button>

      {isLoading ? (
        <EmptyState>Loading...</EmptyState>
      ) : !rec ? (
        <EmptyState>Recommendation not found.</EmptyState>
      ) : (
        <div className="panel" style={{ maxWidth: 720 }}>
          <RecDetailBody rec={rec} />
        </div>
      )}
    </>
  );
}
