import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ReplayRoundedIcon from "@mui/icons-material/ReplayRounded";
import PublishRoundedIcon from "@mui/icons-material/PublishRounded";
import { recommendationsApi } from "../../api/client";
import type { RecommendationDetail as RD, RecommendationStatus } from "../../types";
import { LIFE_LABELS, LifeChip, Modal, PlatformCell, PriorityTag, TypeTag, relTime, usdFmt } from "../ui/ui";

// ── Per-type content sections ─────────────────────────────────────────────────

function Dsec({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="dsec">
      <div className="dl">{label}</div>
      {children}
    </div>
  );
}

function ListSec({ label, items }: { label: string; items: unknown[] | undefined }) {
  if (!items?.length) return null;
  return (
    <Dsec label={label}>
      <ul>
        {items.map((item, i) => <li key={i}>{String(item)}</li>)}
      </ul>
    </Dsec>
  );
}

function ContentBriefBody({ content }: { content: Record<string, unknown> }) {
  const headline = content.headline_suggestion ?? content.headline;
  return (
    <>
      {headline != null && (
        <Dsec label="Headline">
          <p><b>{String(headline)}</b></p>
          {(content.content_type != null || content.recommended_word_count != null) && (
            <p className="dim2" style={{ marginTop: 4 }}>
              {[content.content_type, content.recommended_word_count != null ? `${content.recommended_word_count} words` : null]
                .filter(Boolean).join(", ")}
            </p>
          )}
        </Dsec>
      )}
      <ListSec label="Key questions to answer" items={content.key_questions as unknown[] | undefined} />
      <ListSec label="Recommended structure" items={content.recommended_structure as unknown[] | undefined} />
      <ListSec label="E-E-A-T signals" items={content.eeat_signals as unknown[] | undefined} />
      <ListSec label="Schema types" items={content.schema_types as unknown[] | undefined} />
      {content.competitor_analysis != null && (
        <Dsec label="Competitor analysis"><p>{String(content.competitor_analysis)}</p></Dsec>
      )}
    </>
  );
}

function SchemaBody({ content }: { content: Record<string, unknown> }) {
  const schemas = content.recommended_schemas as Array<Record<string, unknown>> | undefined;
  return (
    <>
      {schemas?.map((s, i) => (
        <Dsec key={i} label={String(s.schema_type ?? "Schema")}>
          {s.purpose != null && <p>{String(s.purpose)}</p>}
          {s.example_jsonld != null && (
            <pre>{typeof s.example_jsonld === "string" ? s.example_jsonld : JSON.stringify(s.example_jsonld, null, 2)}</pre>
          )}
          {s.implementation_notes != null && (
            <p className="dim2" style={{ fontSize: 12, marginTop: 6 }}>{String(s.implementation_notes)}</p>
          )}
        </Dsec>
      ))}
    </>
  );
}

function LlmsTxtBody({ content }: { content: Record<string, unknown> }) {
  const sections = content.new_sections as Array<Record<string, unknown>> | undefined;
  const mods = content.modifications as Array<Record<string, unknown>> | undefined;
  return (
    <>
      {sections?.length ? (
        <Dsec label="New sections">
          {sections.map((s, i) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <p className="mono" style={{ fontSize: 12 }}><b>{String(s.section_title ?? "")}</b></p>
              {s.content != null && <pre style={{ marginTop: 6 }}>{String(s.content)}</pre>}
              {Array.isArray(s.addresses_queries) && s.addresses_queries.length > 0 && (
                <p className="dim2" style={{ fontSize: 11.5, marginTop: 4 }}>
                  Addresses: {(s.addresses_queries as string[]).join(", ")}
                </p>
              )}
            </div>
          ))}
        </Dsec>
      ) : null}
      {mods?.length ? (
        <Dsec label="Modifications">
          <ul>
            {mods.map((m, i) => (
              <li key={i}>
                {m.existing_section != null && <span className="dim2">{String(m.existing_section)}: </span>}
                {String(m.suggested_change ?? "")}
              </li>
            ))}
          </ul>
        </Dsec>
      ) : null}
    </>
  );
}

function GenericBody({ content }: { content: Record<string, unknown> }) {
  const entries = Object.entries(content).filter(([k]) => k !== "reasoning");
  if (!entries.length) return null;
  return (
    <Dsec label="Recommendation">
      <pre>{JSON.stringify(Object.fromEntries(entries), null, 2)}</pre>
    </Dsec>
  );
}

// ── Actions ───────────────────────────────────────────────────────────────────

type ActionKind = "approve" | "reject" | "revise" | "publish";

const ACTION_CFG: Record<ActionKind, { title: string; sub: string; required: boolean; danger?: boolean }> = {
  approve: { title: "Approve, move to In progress", sub: "Optional note for the production team.", required: false },
  reject: { title: "Reject recommendation", sub: "A reason is required, the engine learns from rejections.", required: true, danger: true },
  revise: { title: "Request revision", sub: "Tell the engine what to change, required.", required: true },
  publish: { title: "Mark published", sub: "Link or note about the final published version (fed back to the engine).", required: false },
};

export function RecActions({ rec, onDone }: { rec: RD; onDone?: () => void }) {
  const qc = useQueryClient();
  const [action, setAction] = useState<ActionKind | null>(null);
  const [notes, setNotes] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["recommendation", rec.id] });
    qc.invalidateQueries({ queryKey: ["client-recs"] });
    qc.invalidateQueries({ queryKey: ["rec-summary"] });
    qc.invalidateQueries({ queryKey: ["rec-groups"] });
    qc.invalidateQueries({ queryKey: ["client-rec-group-items"] });
  };

  const mut = useMutation({
    mutationFn: async (kind: ActionKind) => {
      const n = notes.trim();
      if (kind === "approve") return recommendationsApi.approve(rec.id, n || undefined);
      if (kind === "reject") return recommendationsApi.reject(rec.id, n);
      if (kind === "revise") return recommendationsApi.requestRevision(rec.id, n);
      return recommendationsApi.implement(rec.id, n || undefined);
    },
    onSuccess: () => {
      invalidate();
      setAction(null);
      setNotes("");
      onDone?.();
    },
  });

  const status = rec.status as RecommendationStatus;
  const canApprove = status === "pending" || status === "revision_requested";
  const canReject = canApprove;
  const canRevise = status === "pending";
  const canPublish = status === "approved";

  const cfg = action ? ACTION_CFG[action] : null;

  return (
    <Dsec label="Actions">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {canApprove && (
          <button className="btn pri sm" onClick={() => setAction("approve")}>
            <CheckRoundedIcon style={{ fontSize: 13 }} /> Approve, move to In progress
          </button>
        )}
        {canRevise && (
          <button className="btn sm" onClick={() => setAction("revise")}>
            <ReplayRoundedIcon style={{ fontSize: 13 }} /> Request revision
          </button>
        )}
        {canReject && (
          <button className="btn sm danger" onClick={() => setAction("reject")}>
            <CloseRoundedIcon style={{ fontSize: 13 }} /> Reject
          </button>
        )}
        {canPublish && (
          <button className="btn pri sm" onClick={() => setAction("publish")}>
            <PublishRoundedIcon style={{ fontSize: 13 }} /> Mark published
          </button>
        )}
        {!canApprove && !canPublish && (
          <span className="dim" style={{ fontSize: 12 }}>No actions available for this status.</span>
        )}
      </div>
      {canPublish && (
        <div className="footer-note">
          Publishing records the final human-edited version back to the engine so future runs know what is live.
        </div>
      )}

      {action && cfg && (
        <Modal onClose={() => { setAction(null); setNotes(""); }}>
          <h3>{cfg.title}</h3>
          <div className="ms">{cfg.sub}</div>
          <div className="fld">
            <label>Notes {cfg.required ? "*" : ""}</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} autoFocus />
          </div>
          <div className="macts">
            <button className="btn" onClick={() => { setAction(null); setNotes(""); }}>Cancel</button>
            <button
              className={`btn ${cfg.danger ? "danger" : "pri"}`}
              disabled={(cfg.required && !notes.trim()) || mut.isPending}
              onClick={() => mut.mutate(action)}
            >
              {mut.isPending ? "Saving..." : cfg.title}
            </button>
          </div>
        </Modal>
      )}
    </Dsec>
  );
}

// ── Full detail body (drawer + standalone page) ───────────────────────────────

export function RecDetailBody({ rec, onClose, onActionDone }: {
  rec: RD; onClose?: () => void; onActionDone?: () => void;
}) {
  const content = rec.content ?? {};
  const status = rec.status as RecommendationStatus;

  let body: React.ReactNode;
  if (rec.type === "schema_markup") body = <SchemaBody content={content} />;
  else if (rec.type === "llms_txt") body = <LlmsTxtBody content={content} />;
  else if (rec.type === "content_brief") body = <ContentBriefBody content={content} />;
  else body = <GenericBody content={content} />;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <LifeChip status={status} />
        <PriorityTag priority={rec.priority} />
        <TypeTag type={rec.type} />
        <PlatformCell platform={rec.platform} />
        {onClose && (
          <button
            style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0 }}
            onClick={onClose}
            aria-label="Close"
          >
            <CloseRoundedIcon style={{ fontSize: 17 }} />
          </button>
        )}
      </div>

      <h2>{rec.title}</h2>
      <div className="mono dim" style={{ fontSize: 11, marginBottom: 8 }}>
        {relTime(rec.created_at)}
        {rec.run_display_id && <>, run {rec.run_display_id}</>}
        {rec.generation_model && <>, {rec.generation_model}</>}
        {rec.generation_cost_usd != null && <>, gen cost {usdFmt(rec.generation_cost_usd, 2)}</>}
      </div>

      {rec.target_query && (
        <Dsec label="Target query">
          <p style={{ fontStyle: "italic" }}>"{rec.target_query}"</p>
        </Dsec>
      )}

      {body}

      {content.reasoning != null && (
        <Dsec label="Engine reasoning"><p>{String(content.reasoning)}</p></Dsec>
      )}

      <RecActions rec={rec} onDone={onActionDone} />

      {rec.history.length > 0 && (
        <Dsec label="History">
          <div className="hist">
            {rec.history.map((h) => (
              <div key={h.id} className="h">
                <b style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  {h.old_status && (
                    <>
                      {LIFE_LABELS[h.old_status as RecommendationStatus] ?? h.old_status}
                      <ArrowForwardRoundedIcon style={{ fontSize: 11 }} />
                    </>
                  )}
                  {LIFE_LABELS[h.new_status as RecommendationStatus] ?? h.new_status}
                </b>{" "}
                <span className="dim2">by {h.actor}</span>
                <div className="w">{relTime(h.created_at)}{h.notes ? `, ${h.notes}` : ""}</div>
              </div>
            ))}
          </div>
        </Dsec>
      )}
    </>
  );
}
