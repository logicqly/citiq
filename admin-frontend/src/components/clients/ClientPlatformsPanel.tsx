import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import InfoRoundedIcon from "@mui/icons-material/InfoRounded";
import { platformConfigApi } from "../../api/client";
import type { ClientDetail } from "../../types";
import { platMeta, useToast } from "../ui/ui";

const ENGINE_DESC: Record<string, string> = {
  analysis: "Evaluates AI responses for brand citations and competitive gaps.",
  recommendation: "Generates impact-ranked briefs from citation analysis.",
};

const ENGINES = ["analysis", "recommendation"] as const;

/**
 * Per-client platform selection plus the model each platform uses.
 *
 * Turning a platform off removes it from this client's runs entirely, and the
 * engines follow: an analysis or recommendation engine pointed at a disabled
 * platform is moved to an enabled one (mirrored here so the save cannot 422).
 */
export function ClientPlatformsPanel({
  clientId,
  client,
}: {
  clientId: string;
  client: ClientDetail;
}) {
  const qc = useQueryClient();
  const toast = useToast();

  const { data: availableModels } = useQuery({
    queryKey: ["admin-available-models"],
    queryFn: () => platformConfigApi.getAvailableModels(),
  });

  const { data: saved } = useQuery({
    queryKey: ["admin-platform-config", clientId],
    queryFn: () => platformConfigApi.getConfig(clientId),
  });

  const allPlatforms = useMemo(
    () => (availableModels ? Object.keys(availableModels.platforms) : []),
    [availableModels],
  );

  const [modelConfig, setModelConfig] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState<string[]>([]);

  // null from the API means "every platform" — the state of a client that has
  // never been restricted — so it expands to the full list for editing.
  useEffect(() => {
    if (!saved || !allPlatforms.length) return;
    setModelConfig(saved.config);
    setEnabled(saved.enabled_platforms ?? allPlatforms);
  }, [saved, allPlatforms]);

  const savedEnabled = useMemo(
    () => saved?.enabled_platforms ?? allPlatforms,
    [saved, allPlatforms],
  );

  const dirty = useMemo(() => {
    if (!saved) return false;
    const keys = new Set([...Object.keys(saved.config), ...Object.keys(modelConfig)]);
    const modelsChanged = [...keys].some((k) => (modelConfig[k] ?? "") !== (saved.config[k] ?? ""));
    const platformsChanged =
      enabled.length !== savedEnabled.length ||
      [...enabled].sort().join() !== [...savedEnabled].sort().join();
    return modelsChanged || platformsChanged;
  }, [saved, modelConfig, enabled, savedEnabled]);

  const saveMut = useMutation({
    mutationFn: () =>
      // All platforms selected stores as null ("unrestricted"), so a platform
      // added to the engine later applies to this client automatically.
      platformConfigApi.updateConfig(
        clientId,
        modelConfig,
        enabled.length === allPlatforms.length ? null : enabled,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-platform-config", clientId] });
      qc.invalidateQueries({ queryKey: ["admin-client", clientId] });
      toast("Platform settings saved");
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save platform settings", "err"),
  });

  function togglePlatform(platform: string) {
    const isLast = enabled.length === 1 && enabled[0] === platform;
    if (isLast) {
      toast("At least one platform must stay enabled", "err");
      return;
    }
    const next = enabled.includes(platform)
      ? enabled.filter((p) => p !== platform)
      : [...enabled, platform];
    setEnabled(next);

    // Keep the engines on a platform that is still on. Without this the save
    // would be rejected for pointing an engine at a disabled platform.
    setModelConfig((prev) => {
      const updated = { ...prev };
      for (const engine of ENGINES) {
        const pKey = `${engine}_platform`;
        const current = updated[pKey];
        if (current && !next.includes(current)) {
          const replacement = next[0];
          updated[pKey] = replacement;
          updated[`${engine}_model`] =
            availableModels?.defaults[replacement] ??
            availableModels?.platforms[replacement]?.[0] ??
            "";
        }
      }
      return updated;
    });
  }

  if (!availableModels || !saved) {
    return (
      <div className="panel">
        <div className="ph"><h3>AI platforms</h3></div>
        <div className="dim" style={{ fontSize: 12 }}>Loading...</div>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="ph">
        <h3>AI platforms</h3>
        <span className="note">
          {enabled.length === allPlatforms.length
            ? "all platforms"
            : `${enabled.length} of ${allPlatforms.length} enabled`}
        </span>
        <div className="sp" />
        <button
          className="btn sm pri"
          disabled={!dirty || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending ? "Saving..." : "Save changes"}
        </button>
      </div>

      <div style={{ fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.55, marginBottom: 12 }}>
        Which platforms {client.name} is monitored on, and the model each one uses. A platform that
        is turned off is not queried during runs, and cannot be used for citation analysis or
        recommendation generation either.
      </div>

      {allPlatforms.map((platform) => {
        const on = enabled.includes(platform);
        const models = availableModels.platforms[platform] ?? [];
        return (
          <div
            key={platform}
            style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }}
          >
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 9,
                fontSize: 12.5,
                color: on ? "var(--ink2)" : "var(--ink4)",
                cursor: "pointer",
                flex: 1,
                minWidth: 0,
              }}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => togglePlatform(platform)}
                style={{ accentColor: "var(--white)", cursor: "pointer" }}
              />
              <span
                style={{
                  display: "inline-block",
                  width: 7,
                  height: 7,
                  borderRadius: 99,
                  background: platMeta(platform).c,
                  opacity: on ? 1 : 0.4,
                }}
              />
              <span>{platMeta(platform).label}</span>
            </label>
            <select
              value={modelConfig[platform] ?? availableModels.defaults[platform] ?? ""}
              onChange={(e) => setModelConfig((p) => ({ ...p, [platform]: e.target.value }))}
              disabled={!on}
              style={{ flex: 1, minWidth: 0, opacity: on ? 1 : 0.45 }}
            >
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        );
      })}

      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 6,
          fontSize: 11.5,
          color: "var(--ink4)",
          lineHeight: 1.5,
          margin: "14px 0 6px",
        }}
      >
        <InfoRoundedIcon style={{ fontSize: 13, marginTop: 1, flexShrink: 0 }} />
        <span>
          Runs collect one response per prompt per enabled platform, so disabling a platform
          reduces both the cost and the citation data of every future run. Past runs keep the
          platforms they were collected with.
        </span>
      </div>

      <div style={{ borderTop: "1px solid var(--bf)", marginTop: 10, paddingTop: 14 }}>
        {ENGINES.map((engine) => {
          const pKey = `${engine}_platform`;
          const mKey = `${engine}_model`;
          const selectedPlatform = modelConfig[pKey] || enabled[0] || "";
          const platformModels = availableModels.platforms[selectedPlatform] ?? [];
          return (
            <div key={engine} style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink2)" }}>
                {engine === "analysis" ? "Analysis engine" : "Recommendation engine"}
              </label>
              <div className="dim2" style={{ fontSize: 11.5, margin: "2px 0 7px" }}>
                {ENGINE_DESC[engine]}
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <select
                  value={selectedPlatform}
                  style={{ flex: 1, minWidth: 0 }}
                  onChange={(e) => {
                    const next = e.target.value;
                    setModelConfig((p) => ({
                      ...p,
                      [pKey]: next,
                      [mKey]:
                        availableModels.defaults[next] ??
                        availableModels.platforms[next]?.[0] ??
                        "",
                    }));
                  }}
                >
                  {/* Only platforms this client still has on — an engine cannot
                      run on a platform that is switched off for the client. */}
                  {enabled.map((p) => (
                    <option key={p} value={p}>{platMeta(p).label}</option>
                  ))}
                </select>
                <select
                  value={modelConfig[mKey] ?? availableModels.defaults[selectedPlatform] ?? ""}
                  style={{ flex: 1, minWidth: 0 }}
                  onChange={(e) => setModelConfig((p) => ({ ...p, [mKey]: e.target.value }))}
                >
                  {platformModels.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
