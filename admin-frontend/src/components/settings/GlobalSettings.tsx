import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { platformConfigApi, settingsApi } from "../../api/client";
import type { PromptCategoryConfig } from "../../types";
import { useAuth } from "../../auth/AuthContext";
import { Chip, EmptyState, platMeta, useToast } from "../ui/ui";
import { DISPLAY_FIELDS, type DisplayConfig } from "./displayFields";
import { DisplayChecklist } from "./DisplayChecklist";

const DEFAULT_CATEGORY_COLOR = "#3b82f6";
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

const ENGINE_DESC: Record<string, string> = {
  analysis: "Evaluates AI responses for brand citations and competitive gaps.",
  recommendation: "Generates impact-ranked briefs from citation analysis.",
};

const ENGINE_VARS: Record<string, string> = {
  analysis: "{original_prompt}, {raw_response}, {client_brand}, {competitor_list}",
  recommendation: "{client_name}, {industry_context}, {brand_profile}, {target_audience}, {original_prompt}, {platform}, {raw_response_truncated}, {client_cited}, {client_prominence}, {competitors_cited_summary}, {citation_opportunity}, {content_gaps}",
};

// Visibility Score weights, displayed and edited as percentages. The API stores
// them as fractions (0.40 == 40%); Hollow is always 0% (excluded entirely).
const WEIGHT_META: { key: string; label: string }[] = [
  { key: "recommended", label: "Recommended" },
  { key: "mentioned", label: "Neutral mention" },
  { key: "negative", label: "Negative (penalty)" },
  { key: "primary_prominence", label: "Primary prominence" },
  { key: "sentiment", label: "Sentiment" },
  { key: "platform_coverage", label: "Platform coverage" },
];

const ENGINE_KEYS = [
  "analysis_platform", "analysis_model", "analysis_prompt",
  "recommendation_platform", "recommendation_model", "recommendation_prompt",
];

function VisibilityWeightsPanel({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({
    queryKey: ["visibility-weights"],
    queryFn: () => settingsApi.getVisibilityWeights(),
  });

  const [weights, setWeights] = useState<Record<string, number>>({});

  useEffect(() => {
    if (data) setWeights(data.weights);
  }, [data]);

  const saveMut = useMutation({
    mutationFn: () => settingsApi.updateVisibilityWeights(weights),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["visibility-weights"] });
      toast("Visibility weights saved");
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save weights", "err"),
  });

  const dirty = useMemo(() => {
    if (!data) return false;
    return WEIGHT_META.some((m) => (weights[m.key] ?? 0) !== (data.weights[m.key] ?? 0));
  }, [weights, data]);

  const toPct = (frac: number | undefined) => Math.round((frac ?? 0) * 100);
  const setPct = (key: string, pct: number) => setWeights((prev) => ({ ...prev, [key]: pct / 100 }));

  const sumPct = WEIGHT_META.reduce((acc, m) => acc + toPct(weights[m.key]), 0);
  const sumValid = sumPct === 100;

  return (
    <div className="panel">
      <div className="ph">
        <h3>Visibility score weights</h3>
        <span className="note">must sum to 100%</span>
        <div className="sp" />
        <span className="mono" style={{ fontSize: 12, color: sumValid ? "var(--good)" : "var(--bad)" }}>{sumPct}%</span>
        {isSuperAdmin && (
          <button
            className="btn sm pri"
            disabled={!dirty || !sumValid || saveMut.isPending}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? "Saving..." : "Save changes"}
          </button>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
        {WEIGHT_META.map((m) => (
          <div key={m.key} className="fld" style={{ margin: 0 }}>
            <label>{m.label}</label>
            <input
              type="number"
              step={1}
              value={toPct(weights[m.key])}
              onChange={(e) => setPct(m.key, Number(e.target.value))}
              disabled={!isSuperAdmin}
              style={{ fontFamily: "var(--mono)" }}
            />
          </div>
        ))}
      </div>
      <div className="footer-note">Hollow citations fixed at 0%, always excluded.</div>
    </div>
  );
}

function PromptCategoriesPanel({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({
    queryKey: ["prompt-categories"],
    queryFn: () => settingsApi.getPromptCategories(),
  });

  const [cats, setCats] = useState<PromptCategoryConfig[]>([]);

  useEffect(() => {
    if (data) setCats(data);
  }, [data]);

  const saveMut = useMutation({
    mutationFn: () => settingsApi.updatePromptCategories(cats),
    onSuccess: (saved) => {
      setCats(saved);
      qc.invalidateQueries({ queryKey: ["prompt-categories"] });
      toast("Prompt categories saved");
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save categories", "err"),
  });

  const dirty = useMemo(
    () => !!data && JSON.stringify(cats) !== JSON.stringify(data),
    [cats, data],
  );

  const update = (i: number, patch: Partial<PromptCategoryConfig>) =>
    setCats((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => setCats((prev) => prev.filter((_, idx) => idx !== i));
  const add = () => setCats((prev) => [...prev, { name: "", color: DEFAULT_CATEGORY_COLOR, description: "" }]);

  // Client-side validation mirrors the API: at least one category, non-empty
  // unique names, valid hex colors.
  const names = cats.map((c) => c.name.trim().toLowerCase());
  const dupNames = new Set(names.filter((n, i) => n && names.indexOf(n) !== i));
  const invalid =
    cats.length === 0 ||
    cats.some((c) => !c.name.trim() || !HEX_RE.test(c.color)) ||
    dupNames.size > 0;

  return (
    <div className="panel">
      <div className="ph">
        <h3>Prompt categories</h3>
        <span className="note">the taxonomy used across all clients</span>
        <div className="sp" />
        {isSuperAdmin && (
          <>
            <button className="btn sm" onClick={add}>
              <AddRoundedIcon style={{ fontSize: 13 }} /> Add category
            </button>
            <button className="btn sm pri" disabled={!dirty || invalid || saveMut.isPending} onClick={() => saveMut.mutate()}>
              {saveMut.isPending ? "Saving..." : "Save changes"}
            </button>
          </>
        )}
      </div>

      {cats.map((c, i) => {
        const isDup = !!c.name.trim() && dupNames.has(c.name.trim().toLowerCase());
        return (
          <div
            key={i}
            style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--bf)" }}
          >
            <input
              type="color"
              value={HEX_RE.test(c.color) ? c.color : DEFAULT_CATEGORY_COLOR}
              onChange={(e) => update(i, { color: e.target.value })}
              disabled={!isSuperAdmin}
              title={c.color}
              aria-label="Category color"
              style={{ width: 26, height: 26, border: "1px solid var(--b1)", borderRadius: 6, background: "var(--s4)", padding: 2, cursor: isSuperAdmin ? "pointer" : "not-allowed" }}
            />
            <input
              type="text"
              value={c.name}
              onChange={(e) => update(i, { name: e.target.value })}
              disabled={!isSuperAdmin}
              placeholder="Category name"
              style={{
                width: 150, background: "var(--s4)", border: `1px solid ${isDup ? "rgba(229,72,77,.5)" : "var(--b1)"}`,
                borderRadius: 8, color: "var(--ink1)", padding: "7px 10px", fontSize: 13, fontWeight: 650, outline: "none",
              }}
            />
            <input
              type="text"
              value={c.description ?? ""}
              onChange={(e) => update(i, { description: e.target.value })}
              disabled={!isSuperAdmin}
              placeholder="Optional description"
              style={{
                flex: 1, background: "var(--s4)", border: "1px solid var(--b1)", borderRadius: 8,
                color: "var(--ink2)", padding: "7px 10px", fontSize: 12, outline: "none",
              }}
            />
            {isSuperAdmin && (
              <button
                onClick={() => remove(i)}
                aria-label="Delete category"
                title="Delete category"
                style={{ background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0 }}
              >
                <CloseRoundedIcon style={{ fontSize: 15 }} />
              </button>
            )}
          </div>
        );
      })}

      {invalid && cats.length > 0 && (
        <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 10 }}>
          Each category needs a unique name and a valid color before you can save.
        </p>
      )}
    </div>
  );
}

function ClientDisplayDefaultsPanel({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({
    queryKey: ["display-defaults"],
    queryFn: () => settingsApi.getDisplayDefaults(),
  });

  const [cfg, setCfg] = useState<DisplayConfig>({});

  useEffect(() => {
    if (data) setCfg(data);
  }, [data]);

  const saveMut = useMutation({
    mutationFn: () => settingsApi.updateDisplayDefaults(cfg),
    onSuccess: (saved) => {
      setCfg(saved);
      qc.invalidateQueries({ queryKey: ["display-defaults"] });
      toast("Client display defaults saved");
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save display defaults", "err"),
  });

  const dirty = useMemo(
    () => !!data && DISPLAY_FIELDS.some((f) => (cfg[f.key] ?? false) !== (data[f.key] ?? false)),
    [cfg, data],
  );

  return (
    <div className="panel">
      <div className="ph">
        <h3>Client display defaults</h3>
        <span className="note">what new and inheriting clients see in their dashboard</span>
        <div className="sp" />
        {isSuperAdmin && (
          <button className="btn sm pri" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
            {saveMut.isPending ? "Saving..." : "Save changes"}
          </button>
        )}
      </div>
      <div style={{ fontSize: 11.5, color: "var(--ink4)", lineHeight: 1.55, marginBottom: 12 }}>
        Global defaults for every client that follows them. Clients with a customised display keep their own
        setting; changes here never touch them.
      </div>
      <DisplayChecklist
        config={cfg}
        disabled={!isSuperAdmin}
        onToggle={(k) => setCfg((prev) => ({ ...prev, [k]: !prev[k] }))}
      />
    </div>
  );
}

export function GlobalSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";

  // Options come straight from the models the system already supports.
  const { data: availableModels } = useQuery({
    queryKey: ["admin-available-models"],
    queryFn: () => platformConfigApi.getAvailableModels(),
  });

  const { data: globalConfig } = useQuery({
    queryKey: ["global-model-config"],
    queryFn: () => settingsApi.getModelConfig(),
  });

  const [modelConfig, setModelConfig] = useState<Record<string, string>>({});

  useEffect(() => {
    if (globalConfig) setModelConfig(globalConfig.config);
  }, [globalConfig]);

  const saveMut = useMutation({
    mutationFn: () => settingsApi.updateModelConfig(modelConfig),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["global-model-config"] });
      qc.invalidateQueries({ queryKey: ["admin-clients"] });
      qc.invalidateQueries({ queryKey: ["admin-platform-config"] });
      toast(`Saved, applied to ${res.clients_updated} client${res.clients_updated !== 1 ? "s" : ""}`);
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast(err.response?.data?.detail ?? "Failed to save", "err"),
  });

  const platformKeys = availableModels ? Object.keys(availableModels.platforms) : [];
  const keysDirty = (keys: string[]) =>
    !!globalConfig && keys.some((k) => (modelConfig[k] ?? "") !== (globalConfig.config[k] ?? ""));
  const modelDirty = keysDirty(platformKeys);
  const engineDirty = keysDirty(ENGINE_KEYS);

  const update = (key: string, value: string) =>
    setModelConfig((prev) => ({ ...prev, [key]: value }));

  return (
    <>
      <div className="phead">
        <div className="grow">
          <h1 className="page">Settings</h1>
          <div className="sub">System-wide AI configuration applied to every client, super-admin only</div>
        </div>
        {!isSuperAdmin && (
          <Chip><LockOutlinedIcon style={{ fontSize: 11 }} /> View only</Chip>
        )}
      </div>

      {!availableModels ? (
        <EmptyState>Loading settings...</EmptyState>
      ) : (
        <>
          <div className="panel">
            <div className="ph">
              <h3>AI model configuration</h3>
              <span className="note">the model each platform uses for monitoring runs</span>
              <div className="sp" />
              {isSuperAdmin && (
                <button className="btn sm pri" disabled={!modelDirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                  {saveMut.isPending ? "Saving..." : "Save changes"}
                </button>
              )}
            </div>
            <div className="grid2" style={{ margin: 0 }}>
              {Object.entries(availableModels.platforms).map(([platform, models]) => (
                <div key={platform} className="fld" style={{ margin: "0 0 8px" }}>
                  <label>
                    <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 99, background: platMeta(platform).c, marginRight: 6 }} />
                    {platMeta(platform).label}
                  </label>
                  <select
                    value={modelConfig[platform] ?? availableModels.defaults[platform] ?? ""}
                    onChange={(e) => update(platform, e.target.value)}
                    disabled={!isSuperAdmin}
                  >
                    {models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="grid2">
            {(["analysis", "recommendation"] as const).map((engine) => {
              const platformKey = `${engine}_platform`;
              const modelKey = `${engine}_model`;
              const promptKey = `${engine}_prompt`;
              const selectedPlatform = modelConfig[platformKey] || "openai";
              const platformModels = availableModels.platforms[selectedPlatform] ?? [];
              const defaultModel = availableModels.defaults[selectedPlatform] ?? platformModels[0] ?? "";

              return (
                <div key={engine} className="panel" style={{ margin: 0 }}>
                  <div className="ph">
                    <h3>{engine === "analysis" ? "Analysis engine" : "Recommendation engine"}</h3>
                  </div>
                  <p className="dim2" style={{ fontSize: 12, marginBottom: 12 }}>{ENGINE_DESC[engine]}</p>
                  <div style={{ display: "flex", gap: 10 }}>
                    <div className="fld" style={{ flex: 1 }}>
                      <label>Platform</label>
                      <select
                        value={selectedPlatform}
                        onChange={(e) => {
                          const newPlatform = e.target.value;
                          const newModels = availableModels.platforms[newPlatform] ?? [];
                          const newDefault = availableModels.defaults[newPlatform] ?? newModels[0] ?? "";
                          setModelConfig((prev) => ({ ...prev, [platformKey]: newPlatform, [modelKey]: newDefault }));
                        }}
                        disabled={!isSuperAdmin}
                      >
                        {Object.keys(availableModels.platforms).map((p) => (
                          <option key={p} value={p}>{platMeta(p).label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="fld" style={{ flex: 1 }}>
                      <label>Model</label>
                      <select
                        value={modelConfig[modelKey] || defaultModel}
                        onChange={(e) => update(modelKey, e.target.value)}
                        disabled={!isSuperAdmin}
                      >
                        {platformModels.map((m) => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </div>
                  </div>
                  <div className="fld">
                    <label>Custom prompt, optional</label>
                    <textarea
                      value={modelConfig[promptKey] ?? ""}
                      onChange={(e) => update(promptKey, e.target.value)}
                      disabled={!isSuperAdmin}
                      placeholder={`Leave empty to use the default prompt.\n\nVariables: ${ENGINE_VARS[engine]}`}
                      style={{ fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.5 }}
                    />
                  </div>
                  {isSuperAdmin && (
                    <button className="btn sm pri" disabled={!engineDirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                      {saveMut.isPending ? "Saving..." : "Save changes"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <ClientDisplayDefaultsPanel isSuperAdmin={isSuperAdmin} />
          <VisibilityWeightsPanel isSuperAdmin={isSuperAdmin} />
          <PromptCategoriesPanel isSuperAdmin={isSuperAdmin} />
        </>
      )}
    </>
  );
}
