import { Fragment } from "react";
import { useLocation, useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import MenuRoundedIcon from "@mui/icons-material/MenuRounded";
import DarkModeRoundedIcon from "@mui/icons-material/DarkModeRounded";
import LightModeRoundedIcon from "@mui/icons-material/LightModeRounded";
import KeyboardCommandKeyRoundedIcon from "@mui/icons-material/KeyboardCommandKeyRounded";
import { clientsApi } from "../../api/client";
import { useTheme } from "../ui/theme";

function useBreadcrumbs() {
  const location = useLocation();
  const params = useParams<{ clientId?: string; runId?: string; id?: string }>();
  const segments = location.pathname.split("/").filter(Boolean);
  const qc = useQueryClient();

  const { data: client } = useQuery({
    queryKey: ["admin-client", params.clientId],
    queryFn: () => clientsApi.get(params.clientId!),
    enabled: !!params.clientId,
    staleTime: 5 * 60 * 1000,
  });

  // Read run display_id from the cache already populated by RunDetail — no extra fetch
  const runSummary = params.runId
    ? (qc.getQueryData(["admin-run-detail", params.clientId, params.runId]) as { run?: { display_id?: string } } | undefined)
    : undefined;
  const runLabel = runSummary?.run?.display_id ?? params.runId ?? "";

  const crumbs: { label: string; to?: string }[] = [];

  if (segments[0] === "clients") {
    crumbs.push({ label: "Clients", to: "/clients" });

    if (params.clientId) {
      const clientName = client?.name ?? params.clientId;
      if (params.runId) {
        crumbs.push({ label: clientName, to: `/clients/${params.clientId}/runs` });
        crumbs.push({ label: runLabel });
      } else {
        crumbs.push({ label: clientName });
      }
    }
  } else if (segments[0] === "scheduler") {
    crumbs.push({ label: "Scheduler" });
  } else if (segments[0] === "recommendations") {
    crumbs.push({ label: "Recommendations" });
  } else if (segments[0] === "settings") {
    crumbs.push({ label: "Settings" });
  }

  return crumbs;
}

export function TopBar({ onMenuClick }: { onMenuClick: () => void }) {
  const crumbs = useBreadcrumbs();
  const { theme, toggle } = useTheme();

  return (
    <div className="top">
      <button className="iconb menu" onClick={onMenuClick} aria-label="Open menu">
        <MenuRoundedIcon style={{ fontSize: 16 }} />
      </button>

      <div className="crumb">
        {crumbs.map((crumb, i) => {
          const last = i === crumbs.length - 1;
          return (
            <Fragment key={i}>
              {crumb.to && !last ? (
                <Link to={crumb.to}>{crumb.label}</Link>
              ) : last ? (
                <b>{crumb.label}</b>
              ) : (
                <span>{crumb.label}</span>
              )}
              {!last && (
                <span className="sep">
                  <ChevronRightRoundedIcon style={{ fontSize: 13 }} />
                </span>
              )}
            </Fragment>
          );
        })}
      </div>

      <div className="sp" />

      <button className="iconb" onClick={toggle} title="Toggle light / dark" aria-label="Toggle light / dark theme">
        {theme === "light"
          ? <LightModeRoundedIcon style={{ fontSize: 15 }} />
          : <DarkModeRoundedIcon style={{ fontSize: 15 }} />}
      </button>
      <span className="kbd">
        <KeyboardCommandKeyRoundedIcon style={{ fontSize: 10 }} />K
      </span>
    </div>
  );
}
