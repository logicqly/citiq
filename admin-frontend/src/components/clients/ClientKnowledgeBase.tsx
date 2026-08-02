import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { knowledgeBaseApi } from "../../api/client";
import { useToast } from "../ui/ui";

const SECTIONS = [
  { key: "brand_profile", label: "Brand profile" },
  { key: "target_audience", label: "Target audience" },
  { key: "brand_voice", label: "Brand voice" },
  { key: "industry_context", label: "Differentiators" },
] as const;

type SectionKey = (typeof SECTIONS)[number]["key"];

function prettyJson(obj: Record<string, unknown>): string {
  if (Object.keys(obj).length === 0) return "";
  return JSON.stringify(obj, null, 2);
}

function daysAgo(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return "today";
  return `${days}d ago`;
}

export function ClientKnowledgeBase() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const [drafts, setDrafts] = useState<Record<SectionKey, string>>({
    brand_profile: "", target_audience: "", brand_voice: "", industry_context: "",
  });
  const [parseErrors, setParseErrors] = useState<Record<string, string>>({});

  const { data: kb } = useQuery({
    queryKey: ["admin-kb", clientId],
    queryFn: () => knowledgeBaseApi.get(clientId!),
    enabled: !!clientId,
  });

  useEffect(() => {
    if (!kb) return;
    setDrafts({
      brand_profile: prettyJson(kb.brand_profile),
      target_audience: prettyJson(kb.target_audience),
      brand_voice: prettyJson(kb.brand_voice),
      industry_context: prettyJson(kb.industry_context),
    });
  }, [kb]);

  const updateMut = useMutation({
    mutationFn: (body: Record<string, unknown>) => knowledgeBaseApi.update(clientId!, body),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["admin-kb", clientId] });
      toast(`Knowledge base saved as v${saved.version}`);
    },
    onError: () => toast("Failed to save knowledge base", "err"),
  });

  const dirty = useMemo(
    () => !!kb && SECTIONS.some((s) => drafts[s.key] !== prettyJson(kb[s.key])),
    [drafts, kb],
  );

  function handleSave() {
    const errors: Record<string, string> = {};
    const body: Record<string, unknown> = {};
    for (const section of SECTIONS) {
      const raw = drafts[section.key].trim();
      if (!raw) { body[section.key] = {}; continue; }
      try { body[section.key] = JSON.parse(raw); }
      catch { errors[section.key] = "Invalid JSON"; }
    }
    if (Object.keys(errors).length) { setParseErrors(errors); return; }
    setParseErrors({});
    updateMut.mutate(body);
  }

  return (
    <>
      <div className="phead">
        <div className="grow">
          <div className="sub">
            Seeds the client's dedicated agent.{" "}
            {kb && <span className="mono">v{kb.version}, updated {daysAgo(kb.updated_at)}</span>}
          </div>
        </div>
        <button className="btn" onClick={() => toast("Version history is not available yet")}>History</button>
        <button className="btn pri" onClick={handleSave} disabled={updateMut.isPending || !dirty}>
          {updateMut.isPending ? "Saving..." : "Save version"}
        </button>
      </div>

      <div className="grid2">
        {SECTIONS.map((section) => (
          <div key={section.key} className="panel" style={{ margin: 0 }}>
            <div className="ph">
              <h3>{section.label}</h3>
              <span className="note mono">{section.key}</span>
            </div>
            <textarea
              value={drafts[section.key]}
              onChange={(e) => setDrafts((d) => ({ ...d, [section.key]: e.target.value }))}
              spellCheck={false}
              placeholder={`{\n  "key": "value"\n}`}
              style={{
                width: "100%", minHeight: 150, background: "var(--s4)", border: "1px solid var(--b1)",
                borderRadius: 10, color: "var(--ink1)", fontFamily: "var(--mono)", fontSize: 12,
                padding: 12, outline: "none", lineHeight: 1.55, resize: "vertical",
              }}
            />
            {parseErrors[section.key] && (
              <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 6 }}>{parseErrors[section.key]}</p>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
