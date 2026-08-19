import { useEffect, useMemo, useRef, useState } from "react";
import ImageRoundedIcon from "@mui/icons-material/ImageRounded";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import { clientsApi, settingsApi } from "../../api/client";
import type { ClientDetail } from "../../types";
import { EmptyState, useConfirm, useToast } from "../ui/ui";
import type { ConfirmOptions } from "../ui/ui";
import { DISPLAY_FIELDS, resolveDisplayConfig, type DisplayConfig } from "../settings/displayFields";
import { DisplayChecklist } from "../settings/DisplayChecklist";
import { ClientPlatformsPanel } from "./ClientPlatformsPanel";

// Curated list of common IANA timezones with friendly labels.
// The value is the IANA name (what the backend stores + zoneinfo uses).
const TIMEZONES: { value: string; label: string }[] = [
  { value: "Pacific/Honolulu",               label: "Hawaii (UTC-10)" },
  { value: "America/Anchorage",              label: "Alaska (UTC-9)" },
  { value: "America/Los_Angeles",            label: "US Pacific - LA / Seattle (UTC-8/-7)" },
  { value: "America/Denver",                 label: "US Mountain - Denver (UTC-7/-6)" },
  { value: "America/Phoenix",                label: "US Mountain - Phoenix (UTC-7, no DST)" },
  { value: "America/Chicago",                label: "US Central - Chicago (UTC-6/-5)" },
  { value: "America/New_York",               label: "US Eastern - New York (UTC-5/-4)" },
  { value: "America/Halifax",                label: "Atlantic - Halifax (UTC-4/-3)" },
  { value: "America/Sao_Paulo",              label: "Sao Paulo (UTC-3/-2)" },
  { value: "America/Argentina/Buenos_Aires", label: "Buenos Aires (UTC-3)" },
  { value: "UTC",                            label: "UTC (UTC+0)" },
  { value: "Europe/London",                  label: "London (UTC+0/+1)" },
  { value: "Europe/Paris",                   label: "Paris / Berlin / Rome (UTC+1/+2)" },
  { value: "Europe/Helsinki",                label: "Helsinki / Kyiv (UTC+2/+3)" },
  { value: "Europe/Moscow",                  label: "Moscow (UTC+3)" },
  { value: "Asia/Dubai",                     label: "Dubai / Abu Dhabi (UTC+4)" },
  { value: "Asia/Karachi",                   label: "Karachi (UTC+5)" },
  { value: "Asia/Kolkata",                   label: "India - Mumbai / Delhi (UTC+5:30)" },
  { value: "Asia/Colombo",                   label: "Sri Lanka (UTC+5:30)" },
  { value: "Asia/Dhaka",                     label: "Dhaka / Almaty (UTC+6)" },
  { value: "Asia/Bangkok",                   label: "Bangkok / Jakarta (UTC+7)" },
  { value: "Asia/Singapore",                 label: "Singapore / Kuala Lumpur (UTC+8)" },
  { value: "Asia/Shanghai",                  label: "China (UTC+8)" },
  { value: "Asia/Tokyo",                     label: "Japan / South Korea (UTC+9)" },
  { value: "Australia/Perth",                label: "Perth (UTC+8)" },
  { value: "Australia/Adelaide",             label: "Adelaide (UTC+9:30/+10:30)" },
  { value: "Australia/Sydney",               label: "Sydney / Melbourne (UTC+10/+11)" },
  { value: "Pacific/Auckland",               label: "New Zealand (UTC+12/+13)" },
];

function ClientDisplayPanel({ clientId, client }: { clientId: string; client: ClientDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const customised = client.display_config != null;

  // The global defaults, shown (read-only) when this client is still inheriting.
  const { data: globalDefaults } = useQuery({
    queryKey: ["display-defaults"],
    queryFn: () => settingsApi.getDisplayDefaults(),
  });

  // Working copy: the client's own config when customised, else the global
  // defaults (rendered disabled).
  const effective = useMemo(
    () => resolveDisplayConfig(customised ? client.display_config : globalDefaults),
    [customised, client.display_config, globalDefaults],
  );
  const [cfg, setCfg] = useState<DisplayConfig>(effective);
  useEffect(() => { setCfg(effective); }, [effective]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["admin-client", clientId] });
    qc.invalidateQueries({ queryKey: ["admin-clients"] });
  };

  const customiseMut = useMutation({
    mutationFn: () => clientsApi.updateDisplay(clientId, resolveDisplayConfig(globalDefaults)),
    onSuccess: () => { invalidate(); toast(`${client.name} display customised, now detached from global defaults`); },
    onError: () => toast("Failed to customise display", "err"),
  });
  const revertMut = useMutation({
    mutationFn: () => clientsApi.revertDisplay(clientId),
    onSuccess: () => { invalidate(); toast(`${client.name} reverted to global defaults`); },
    onError: () => toast("Failed to revert display", "err"),
  });
  const saveMut = useMutation({
    mutationFn: () => clientsApi.updateDisplay(clientId, cfg),
    onSuccess: () => { invalidate(); toast("Client display saved"); },
    onError: () => toast("Failed to save display", "err"),
  });

  const busy = customiseMut.isPending || revertMut.isPending || saveMut.isPending;
  const dirty = useMemo(() => {
    if (!customised) return false;
    const saved = resolveDisplayConfig(client.display_config);
    return DISPLAY_FIELDS.some((f) => (cfg[f.key] ?? false) !== (saved[f.key] ?? false));
  }, [cfg, customised, client.display_config]);

  return (
    <div className="panel">
      <div className="ph">
        <h3>Client display</h3>
        <span className="note">{customised ? "customised for this client" : "following global defaults"}</span>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.55, marginBottom: 12 }}>
        Controls the client-facing GEO Monitor for {client.name}. Unchecked items are removed from their app
        entirely: nav tabs, columns and widgets included.
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.5, marginBottom: 14 }}>
        {customised ? (
          <>
            <span style={{ flex: 1, minWidth: 200 }}>
              Customised. This client is detached from the global defaults, so global changes no longer affect it.
            </span>
            <button className="btn sm" disabled={busy} onClick={() => revertMut.mutate()}>
              Revert to global defaults
            </button>
          </>
        ) : (
          <>
            <span style={{ flex: 1, minWidth: 200 }}>
              Following the global defaults. Changes made in Settings, Client display defaults apply to this
              client automatically.
            </span>
            <button className="btn sm" disabled={busy || !globalDefaults} onClick={() => customiseMut.mutate()}>
              Customise for this client
            </button>
          </>
        )}
      </div>
      <DisplayChecklist
        config={cfg}
        disabled={!customised}
        onToggle={(k) => setCfg((prev) => ({ ...prev, [k]: !prev[k] }))}
      />
      {customised && (
        <button className="btn pri" style={{ marginTop: 12 }} disabled={!dirty || busy} onClick={() => saveMut.mutate()}>
          {saveMut.isPending ? "Saving..." : "Save display settings"}
        </button>
      )}
    </div>
  );
}

function ClientLogoPanel({ clientId, client }: { clientId: string; client: ClientDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const fileInput = useRef<HTMLInputElement>(null);

  // The logo endpoint needs the admin bearer token, so the preview cannot be a
  // plain <img src>. Fetch the bytes and render an object URL instead. Keyed on
  // logo_updated_at so a re-upload replaces the preview instead of showing the
  // cached one.
  const { data: blob } = useQuery({
    queryKey: ["admin-client-logo", clientId, client.logo_updated_at],
    queryFn: () => clientsApi.getLogoBlob(clientId),
    enabled: client.has_logo,
  });

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) { setPreviewUrl(null); return; }
    const url = URL.createObjectURL(blob);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["admin-client", clientId] });
    qc.invalidateQueries({ queryKey: ["admin-clients"] });
    qc.invalidateQueries({ queryKey: ["admin-client-logo", clientId] });
  };

  const uploadMut = useMutation({
    mutationFn: (file: File) => clientsApi.uploadLogo(clientId, file),
    onSuccess: () => { invalidate(); toast("Logo saved"); },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast(detail || "Failed to upload logo", "err");
    },
  });

  const removeMut = useMutation({
    mutationFn: () => clientsApi.deleteLogo(clientId),
    onSuccess: () => { invalidate(); toast("Logo removed"); },
    onError: () => toast("Failed to remove logo", "err"),
  });

  const busy = uploadMut.isPending || removeMut.isPending;

  function pickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset the input so re-picking the same file still fires a change event.
    e.target.value = "";
    if (file) uploadMut.mutate(file);
  }

  async function removeLogo() {
    const ok = await confirm({
      title: "Remove logo?",
      message: `${client.name}'s dashboard and reports fall back to the Citiq mark and text only.`,
      confirmLabel: "Remove logo",
      danger: true,
    });
    if (ok) removeMut.mutate();
  }

  return (
    <div className="panel">
      <div className="ph">
        <h3>Brand logo</h3>
        <span className="note">{client.has_logo ? client.logo_filename : "not set"}</span>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.55, marginBottom: 14 }}>
        Shown in {client.name}'s GEO Monitor header and printed on the cover of their generated
        reports. PNG or SVG, up to 512 KB. A transparent background works best on both themes.
      </div>

      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          minHeight: 96, padding: 16, marginBottom: 14,
          border: "1px solid var(--bf)", borderRadius: 10, background: "var(--s4)",
        }}
      >
        {previewUrl ? (
          <img
            src={previewUrl}
            alt={`${client.name} logo`}
            style={{ maxWidth: "100%", maxHeight: 64, objectFit: "contain" }}
          />
        ) : (
          <div className="dim" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <ImageRoundedIcon style={{ fontSize: 16 }} />
            {client.has_logo ? "Loading..." : "No logo uploaded"}
          </div>
        )}
      </div>

      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/svg+xml,.png,.svg"
        style={{ display: "none" }}
        onChange={pickFile}
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn pri" disabled={busy} onClick={() => fileInput.current?.click()}>
          {uploadMut.isPending ? "Uploading..." : client.has_logo ? "Replace logo" : "Upload logo"}
        </button>
        {client.has_logo && (
          <button className="btn sm" disabled={busy} onClick={removeLogo}>
            {removeMut.isPending ? "Removing..." : "Remove"}
          </button>
        )}
      </div>
    </div>
  );
}

function DangerRow({ title, sub, action }: { title: string; sub: string; action: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, border: "1px solid var(--bf)", borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ flex: 1 }}>
        <b style={{ fontSize: 13 }}>{title}</b>
        <div className="dim" style={{ fontSize: 12 }}>{sub}</div>
      </div>
      {action}
    </div>
  );
}

export function ClientSettings() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("");
  const [website, setWebsite] = useState("");
  const [timezone, setTimezone] = useState("UTC");

  useEffect(() => {
    if (!client) return;
    setName(client.name);
    setIndustry(client.industry ?? "");
    setWebsite(client.website ?? "");
    setTimezone(client.timezone ?? "UTC");
  }, [client]);

  const updateMut = useMutation({
    mutationFn: () =>
      clientsApi.update(clientId!, {
        name: name.trim(),
        industry: industry.trim() || undefined,
        website: website.trim() || undefined,
        timezone,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-client", clientId] });
      qc.invalidateQueries({ queryKey: ["admin-clients"] });
      // Also recompute schedule next-run if schedule is active
      qc.invalidateQueries({ queryKey: ["admin-schedule", clientId] });
      toast("Client saved");
    },
    onError: () => toast("Failed to save client", "err"),
  });

  const statusMut = useMutation({
    mutationFn: (s: string) => clientsApi.setStatus(clientId!, s),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["admin-client", clientId] });
      qc.invalidateQueries({ queryKey: ["admin-clients"] });
      toast(`Client ${updated.status}`);
      if (updated.status === "archived") navigate("/clients");
    },
  });

  const dirty = useMemo(() => {
    if (!client) return false;
    return (
      name !== client.name ||
      industry !== (client.industry ?? "") ||
      website !== (client.website ?? "") ||
      timezone !== (client.timezone ?? "UTC")
    );
  }, [client, name, industry, website, timezone]);

  if (!client) return <EmptyState>Loading...</EmptyState>;

  async function setStatus(s: string, confirmation?: ConfirmOptions) {
    if (confirmation && !(await confirm(confirmation))) return;
    statusMut.mutate(s);
  }

  return (
    <div className="grid2">
      <div className="panel">
        <div className="ph"><h3>General</h3></div>
        <div className="fld">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="fld">
          <label>Industry</label>
          <input value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="HR & Payroll Software" />
        </div>
        <div className="fld">
          <label>Website</label>
          <input type="url" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://example.com" />
        </div>
        <div className="fld">
          <label>Timezone</label>
          <select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {TIMEZONES.map((tz) => <option key={tz.value} value={tz.value}>{tz.label}</option>)}
          </select>
          <div className="fh">All schedule times are interpreted in this timezone.</div>
        </div>
        <div className="fld">
          <label>Slug</label>
          <input value={client.slug} disabled style={{ opacity: 0.5, fontFamily: "var(--mono)" }} />
        </div>
        <button className="btn pri" disabled={updateMut.isPending || !name.trim() || !dirty} onClick={() => updateMut.mutate()}>
          {updateMut.isPending ? "Saving..." : "Save changes"}
        </button>
      </div>

      <ClientPlatformsPanel clientId={clientId!} client={client} />

      <ClientLogoPanel clientId={clientId!} client={client} />

      <ClientDisplayPanel clientId={clientId!} client={client} />

      <div className="panel" style={{ borderColor: "rgba(229,72,77,.25)" }}>
        <div className="ph"><h3 style={{ color: "var(--bad)" }}>Danger zone</h3></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {client.status !== "archived" && (
            <DangerRow
              title={client.status === "active" ? "Pause client" : "Reactivate client"}
              sub="Stops scheduled runs; data is retained."
              action={
                <button
                  className="btn sm"
                  disabled={statusMut.isPending}
                  onClick={() =>
                    client.status === "active"
                      ? setStatus("paused", {
                          title: "Pause client?",
                          message: "Scheduled runs stop until the client is reactivated. Data is retained.",
                          confirmLabel: "Pause client",
                        })
                      : setStatus("active")
                  }
                >
                  {client.status === "active" ? "Pause" : "Reactivate"}
                </button>
              }
            />
          )}
          {client.status !== "archived" ? (
            <DangerRow
              title="Archive client"
              sub="Hidden from the console; recoverable by an engineer."
              action={
                <button
                  className="btn sm danger"
                  disabled={statusMut.isPending}
                  onClick={() =>
                    setStatus("archived", {
                      title: "Archive client?",
                      message: "The client is hidden from the console; recoverable by an engineer.",
                      confirmLabel: "Archive client",
                      danger: true,
                    })
                  }
                >
                  Archive
                </button>
              }
            />
          ) : (
            <DangerRow
              title="Unarchive client"
              sub="Restore this client to the active state."
              action={
                <button className="btn sm" disabled={statusMut.isPending} onClick={() => setStatus("active")}>
                  Unarchive
                </button>
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
