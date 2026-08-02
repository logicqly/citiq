import { useQueries, useQuery } from "@tanstack/react-query";
import { clientsApi, recommendationsApi } from "../../api/client";
import type { ClientSummary } from "../../types";

// The recommendations endpoints are per-client (client_id is required), so the
// global review-queue numbers are aggregated from one summary call per client.
// Cached for a minute; the client list itself is shared with the Clients page.
export function usePendingReview() {
  const { data: clients = [] } = useQuery({
    queryKey: ["admin-clients", ""],
    queryFn: () => clientsApi.list(""),
    staleTime: 60_000,
  });

  const summaries = useQueries({
    queries: (clients as ClientSummary[]).map((c) => ({
      queryKey: ["rec-summary", c.id],
      queryFn: () => recommendationsApi.summary(c.id),
      staleTime: 60_000,
    })),
  });

  const byClient = new Map<string, number>();
  (clients as ClientSummary[]).forEach((c, i) => {
    const pending = summaries[i]?.data?.by_status?.pending ?? 0;
    byClient.set(c.id, pending);
  });

  let total = 0;
  let clientsWithPending = 0;
  byClient.forEach((n) => {
    total += n;
    if (n > 0) clientsWithPending += 1;
  });

  return { clients: clients as ClientSummary[], byClient, total, clientsWithPending };
}
