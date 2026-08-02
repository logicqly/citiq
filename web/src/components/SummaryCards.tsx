import type { CitationQuality, RunSummaryResponse } from "../lib/types";
import type { DisplayConfig } from "../lib/display";
import { EmptyState, HBars, BarMeter, pctFmt, platMeta } from "./ui";

const QUALITY_META: { key: "recommended" | "mentioned" | "negative"; label: string; c: string }[] = [
  { key: "recommended", label: "Recommended", c: "var(--good)" },
  { key: "mentioned", label: "Neutral mention", c: "var(--ink5)" },
  { key: "negative", label: "Negative", c: "var(--bad)" },
];

export function CitationQualityPanel({ quality, hollowCount }: { quality: CitationQuality | null | undefined; hollowCount: number }) {
  return (
    <div className="panel">
      <div className="ph">
        <h3>Citation quality</h3>
        <span className="note">
          {quality ? `${quality.effective_total} real citations, ${hollowCount} hollow excluded` : "no data yet"}
        </span>
      </div>
      {quality && quality.effective_total > 0 ? (
        <>
          <div className="qbar">
            {QUALITY_META.map(({ key, c }) => {
              const w = Math.round(quality[`${key}_pct`] * 100);
              if (w === 0) return null;
              return <i key={key} style={{ width: `${w}%`, background: c }} />;
            })}
          </div>
          {QUALITY_META.map(({ key, label, c }) => (
            <div key={key} className="qrow">
              <span className="d" style={{ background: c }} />
              {label}
              <span className="r">{Math.round(quality[`${key}_pct`] * 100)}%, {quality[key]}</span>
            </div>
          ))}
          <div className="qrow" style={{ color: "var(--ink4)" }}>
            <span className="d" style={{ background: "var(--s4)", border: "1px solid var(--b2)" }} />
            Hollow (excluded)
            <span className="r">{hollowCount}</span>
          </div>
        </>
      ) : (
        <EmptyState>No substantive citations in this run.</EmptyState>
      )}
    </div>
  );
}

export function CompetitorSovPanel({ summary }: { summary: RunSummaryResponse }) {
  const stats = summary.competitor_stats ?? [];
  return (
    <div className="panel">
      <div className="ph">
        <h3>Competitor share of voice</h3>
        <span className="note">who AI cites in your category</span>
      </div>
      {stats.length > 0 ? (
        <HBars
          rows={stats.slice(0, 6).map((c) => ({
            label: c.brand,
            v: c.share_of_voice,
            right: `${pctFmt(c.share_of_voice)}, ${c.cited_count}`,
          }))}
        />
      ) : (
        <EmptyState>No competitor citations in this run.</EmptyState>
      )}
    </div>
  );
}

export function ByPlatformPanel({ summary, showModelIds = true }: { summary: RunSummaryResponse; showModelIds?: boolean }) {
  return (
    <div className="panel">
      <div className="ph">
        <h3>By platform</h3>
        <span className="note">{showModelIds ? "cited prompts / total, model" : "cited prompts / total"}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 11 }}>
        {summary.platform_stats.map((s) => {
          const p = platMeta(s.platform);
          const failed = !!summary.platform_errors?.[s.platform];
          return (
            <div key={s.platform} style={{ border: "1px solid var(--bf)", borderRadius: 11, padding: "13px 15px", opacity: failed ? 0.55 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: 99, background: p.c, flexShrink: 0 }} />
                <b style={{ fontSize: 12.5 }}>{p.label}</b>
              </div>
              {showModelIds && <div className="mono dim" style={{ fontSize: 10, marginTop: 2 }}>{s.model_used}</div>}
              {failed ? (
                <div className="chip bad" style={{ marginTop: 10 }}><span className="d" />failed</div>
              ) : (
                <>
                  <div className="mono" style={{ fontSize: 21, marginTop: 9 }}>
                    {s.cited_count}
                    <span style={{ fontSize: 12, color: "var(--ink4)" }}>/{s.total_responses}</span>
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <BarMeter
                      pct={s.total_responses > 0 ? (s.cited_count / s.total_responses) * 100 : 0}
                      color={p.c}
                    />
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SummaryCards({ summary, display }: { summary: RunSummaryResponse; display: DisplayConfig }) {
  const quality = (
    <CitationQualityPanel quality={summary.citation_quality} hollowCount={summary.hollow_citation_count ?? 0} />
  );
  const sov = <CompetitorSovPanel summary={summary} />;

  return (
    <>
      {display.quality && display.sov ? (
        <div className="grid2">
          {quality}
          {sov}
        </div>
      ) : display.quality ? (
        quality
      ) : display.sov ? (
        sov
      ) : null}
      {display.platforms && <ByPlatformPanel summary={summary} showModelIds={display.model_ids} />}
    </>
  );
}
