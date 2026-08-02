import { NavLink, Outlet, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { clientsApi, recommendationsApi } from "../../api/client";
import { ClientStatusChip, getInitials } from "../ui/ui";

const TABS = [
  { to: "overview", label: "Overview" },
  { to: "prompts", label: "Prompts" },
  { to: "competitors", label: "Competitors" },
  { to: "knowledge-base", label: "Knowledge base" },
  { to: "runs", label: "Runs" },
  { to: "recommendations", label: "Recommendations" },
  { to: "schedule", label: "Schedule" },
  { to: "users", label: "Users" },
  { to: "settings", label: "Settings" },
];

export function ClientDetail() {
  const { clientId } = useParams<{ clientId: string }>();

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const { data: recSummary } = useQuery({
    queryKey: ["rec-summary", clientId],
    queryFn: () => recommendationsApi.summary(clientId!),
    enabled: !!clientId,
    staleTime: 60_000,
  });
  const pending = recSummary?.by_status?.pending ?? 0;

  return (
    <>
      <div className="chead">
        <div className="cav">{client ? getInitials(client.name) : "--"}</div>
        <div>
          <h1>{client?.name ?? "Loading..."}</h1>
          <div className="meta">
            {client && [
              client.industry,
              client.website,
              client.timezone,
            ].filter(Boolean).map((part, i, arr) => (
              <span key={i}>
                {part === client.website && client.website ? (
                  <a
                    href={client.website.startsWith("http") ? client.website : `https://${client.website}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--ink3)" }}
                  >
                    {client.website.replace(/^https?:\/\//, "")}
                  </a>
                ) : part}
                {i < arr.length - 1 && ", "}
              </span>
            ))}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        {client && <ClientStatusChip status={client.status} />}
      </div>

      <div className="tabs">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} className={({ isActive }) => `t${isActive ? " on" : ""}`}>
            {tab.label}
            {tab.to === "recommendations" && pending > 0 && (
              <span className="n" style={{ color: "var(--warn)" }}>{pending}</span>
            )}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </>
  );
}
