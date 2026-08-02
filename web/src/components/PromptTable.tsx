import { useState } from "react";
import KeyboardArrowDownRoundedIcon from "@mui/icons-material/KeyboardArrowDownRounded";
import KeyboardArrowRightRoundedIcon from "@mui/icons-material/KeyboardArrowRightRounded";
import type { PromptAnalysisItem, PromptDetail } from "../lib/types";
import { Chip, EmptyState, platMeta } from "./ui";

function analysisSummary(item: PromptAnalysisItem): string {
  if (item.client_cited == null) return "Analysis pending.";
  const parts: string[] = [];
  if (item.client_cited) {
    parts.push(
      item.citation_type === "recommended" ? "Cited, recommended" :
      item.citation_type === "negative" ? "Cited, negative" :
      item.citation_type === "hollow" ? "Cited, hollow" : "Cited, mentioned"
    );
    if (item.client_prominence && item.client_prominence !== "not_cited") parts.push(`Prominence: ${item.client_prominence}`);
    if (item.client_sentiment && item.client_sentiment !== "not_cited") parts.push(`Sentiment: ${item.client_sentiment}`);
  } else {
    parts.push("Not cited");
  }
  if (item.opportunity_score != null) {
    parts.push(`Opportunity: ${item.opportunity_score.toFixed(1)}/5.0`);
  } else if (item.citation_opportunity) {
    parts.push(`Opportunity: ${item.citation_opportunity}`);
  }
  if (item.competitors_cited.length > 0) {
    parts.push(`Competitors also cited: ${item.competitors_cited.map((c) => c.brand).join(", ")}`);
  }
  if (item.content_gaps.length > 0) {
    parts.push(`Content gaps: ${item.content_gaps.slice(0, 2).join("; ")}`);
  }
  return parts.join(". ") + ".";
}

function PlatformResult({ item, showResponses, showModelIds }: { item: PromptAnalysisItem; showResponses: boolean; showModelIds: boolean }) {
  const [showFull, setShowFull] = useState(false);
  const p = platMeta(item.platform);
  const truncated = item.raw_response.length > 280 && !showFull;
  const displayText = truncated ? item.raw_response.slice(0, 280) + "..." : item.raw_response;

  return (
    <div className="plat">
      <div className="phd">
        <span style={{ width: 7, height: 7, borderRadius: 99, background: p.c, flexShrink: 0 }} />
        <b>{p.label}</b>
        {showModelIds && item.model_used && <span className="mono">{item.model_used}</span>}
        <div style={{ flex: 1 }} />
        {item.client_cited != null && (
          item.client_cited ? <Chip tone="good">Cited</Chip> : <Chip>Not cited</Chip>
        )}
      </div>
      {showResponses && (
        <div className="cols">
          <div>
            <div className="cl">Response</div>
            <p>
              {displayText}
              {item.raw_response.length > 280 && (
                <button
                  onClick={() => setShowFull(!showFull)}
                  style={{ marginLeft: 4, background: "none", border: "none", color: "var(--ink1)", fontSize: 11, fontWeight: 600, textDecoration: "underline", padding: 0 }}
                >
                  {showFull ? "show less" : "more"}
                </button>
              )}
            </p>
          </div>
          <div>
            <div className="cl">Analysis</div>
            <p>{analysisSummary(item)}</p>
            {item.client_characterization && (
              <p className="dim2" style={{ marginTop: 6, fontStyle: "italic" }}>"{item.client_characterization}"</p>
            )}
            {item.reasoning && <p className="dim2" style={{ marginTop: 6 }}>{item.reasoning}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function PromptRow({ detail, showResponses, showModelIds }: { detail: PromptDetail; showResponses: boolean; showModelIds: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const citedCount = detail.results.filter((r) => r.client_cited).length;
  const total = detail.results.length;

  return (
    <div className="pr">
      <button className="head" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        <span className="dim" style={{ display: "inline-flex" }}>
          {expanded
            ? <KeyboardArrowDownRoundedIcon style={{ fontSize: 15 }} />
            : <KeyboardArrowRightRoundedIcon style={{ fontSize: 15 }} />}
        </span>
        <span className="tx">{detail.prompt_text}</span>
        {detail.category && <span className="tag">{detail.category}</span>}
        <span
          className="mono"
          style={{ fontSize: 11, color: citedCount === total && total > 0 ? "var(--good)" : citedCount > 0 ? "var(--warn)" : "var(--ink4)" }}
        >
          {citedCount}/{total} cited
        </span>
        <span className="dots">
          {detail.results.map((r) => {
            const p = platMeta(r.platform);
            return (
              <i
                key={r.platform}
                className={r.client_cited ? "c" : ""}
                style={r.client_cited ? { background: p.c } : undefined}
                title={`${p.label}: ${r.client_cited == null ? "pending" : r.client_cited ? "cited" : "not cited"}`}
              />
            );
          })}
        </span>
      </button>

      {expanded && (
        <div className="body">
          {detail.results.map((item) => (
            <PlatformResult key={item.response_id} item={item} showResponses={showResponses} showModelIds={showModelIds} />
          ))}
        </div>
      )}
    </div>
  );
}

export function PromptTable({ prompts, showResponses = true, showModelIds = true }: { prompts: PromptDetail[]; showResponses?: boolean; showModelIds?: boolean }) {
  const [filter, setFilter] = useState<"all" | "cited" | "not_cited">("all");

  const filtered = prompts.filter((p) => {
    if (filter === "cited") return p.results.some((r) => r.client_cited);
    if (filter === "not_cited") return p.results.every((r) => !r.client_cited);
    return true;
  });

  return (
    <div className="panel">
      <div className="ph">
        <h3>Prompts</h3>
        <span className="note">how each AI answered</span>
        <div className="sp" />
        <div className="pillrow">
          {(["all", "cited", "not_cited"] as const).map((f) => (
            <button key={f} className={`pi${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f === "all" ? `All (${prompts.length})` : f === "cited" ? "Cited" : "Not cited"}
            </button>
          ))}
        </div>
      </div>
      {filtered.map((p) => (
        <PromptRow key={p.prompt_id} detail={p} showResponses={showResponses} showModelIds={showModelIds} />
      ))}
      {filtered.length === 0 && <EmptyState>No prompts match this filter.</EmptyState>}
    </div>
  );
}
