import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import EditRoundedIcon from "@mui/icons-material/EditRounded";
import FileDownloadRoundedIcon from "@mui/icons-material/FileDownloadRounded";
import FileUploadRoundedIcon from "@mui/icons-material/FileUploadRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import { promptsApi, runsApi, settingsApi } from "../../api/client";
import type { Prompt, PromptCategoryConfig } from "../../types";
import { BarMeter, EmptyState, Modal, PillRow, SearchBox, TSwitch, useToast } from "../ui/ui";

// ── Constants & helpers ──────────────────────────────────────────────────────

const MAX_JSON_BYTES = 2 * 1024 * 1024;
const FALLBACK_COLOR = "var(--ink4)";

function CategoryCell({ category, colorByName }: { category: string; colorByName: Map<string, string> }) {
  if (!category) return <span className="dim">-</span>;
  return (
    <>
      <span className="catd" style={{ background: colorByName.get(category) ?? FALLBACK_COLOR }} />
      <span style={{ fontSize: 12, color: "var(--ink2)" }}>{category}</span>
    </>
  );
}

// ── JSON upload types & helpers ───────────────────────────────────────────────

interface ParsedPrompt { text: string; category: string }
interface ParsedRow {
  index: number;
  prompt: ParsedPrompt;
  rawCategory: string;
  unknownCategory: boolean;
  errors: string[];
}

function validateRow(p: unknown, index: number, validNames: Map<string, string>): ParsedRow {
  const errors: string[] = [];
  const raw = p as Record<string, unknown>;
  const text = typeof raw?.text === "string" ? raw.text.trim() : "";
  const rawCategory = typeof raw?.category === "string" ? raw.category.trim() : "";
  if (!text || text.length < 10) errors.push("Text must be at least 10 characters");
  else if (text.length > 500) errors.push("Text must be at most 500 characters");
  // Category is optional; an unknown category is imported blank (not an error).
  const canonical = rawCategory ? validNames.get(rawCategory.toLowerCase()) : undefined;
  return {
    index,
    prompt: { text, category: canonical ?? "" },
    rawCategory,
    unknownCategory: !!rawCategory && !canonical,
    errors,
  };
}

function downloadJsonTemplate(categoryNames: string[]) {
  const examples = [
    "What is the best [product category] for [use case]?",
    "[Brand A] vs [Brand B]",
    "What should I look for when choosing a [product category]?",
    "Best [product category] for [specific use case]",
    "Most trusted [product category] providers",
  ];
  const template = examples.map((text, i) => ({
    text,
    category: categoryNames.length ? categoryNames[i % categoryNames.length] : "",
  }));
  const blob = new Blob([JSON.stringify(template, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "prompt_template.json"; a.click();
  URL.revokeObjectURL(url);
}

// ── JSON upload modal ─────────────────────────────────────────────────────────

function JsonUploadModal({ clientId, categories, onClose, onSuccess }: {
  clientId: string; categories: PromptCategoryConfig[]; onClose: () => void; onSuccess: (msg: string) => void;
}) {
  const qc = useQueryClient();
  const validNames = useMemo(
    () => new Map(categories.map((c) => [c.name.toLowerCase(), c.name])),
    [categories],
  );
  const [dragOver, setDragOver] = useState(false);
  const [rows, setRows] = useState<ParsedRow[] | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const importMut = useMutation({
    mutationFn: (prompts: ParsedPrompt[]) => promptsApi.bulkCreate(clientId, prompts),
    onSuccess: (data: { created: number; skipped: number }) => {
      qc.invalidateQueries({ queryKey: ["admin-prompts", clientId] });
      onSuccess(
        `${data.created} prompt${data.created !== 1 ? "s" : ""} imported` +
        (data.skipped ? `, ${data.skipped} skipped as duplicates` : "")
      );
      onClose();
    },
  });

  function processFile(file: File) {
    setParseError(null); setRows(null);
    if (file.size > MAX_JSON_BYTES) { setParseError("File exceeds 2 MB limit."); return; }
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target!.result as string);
        const arr: unknown[] = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.prompts) ? parsed.prompts : null!;
        if (!Array.isArray(arr)) { setParseError('Expected a JSON array or an object with a "prompts" array.'); return; }
        setRows(arr.map((item, i) => validateRow(item, i, validNames)));
      } catch { setParseError("This file is not valid JSON."); }
    };
    reader.readAsText(file);
  }

  const validRows = rows?.filter((r) => r.errors.length === 0) ?? [];
  const invalidCount = (rows?.length ?? 0) - validRows.length;

  return (
    <Modal onClose={onClose} wide>
      <h3>Upload prompts JSON</h3>
      <div className="ms">Array of {"{text, category}"}, max 2MB, validated before import.</div>

      {!rows && !parseError && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) processFile(f); }}
          onClick={() => fileRef.current?.click()}
          role="button"
          style={{
            border: `1.5px dashed ${dragOver ? "var(--bs)" : "var(--b2)"}`,
            borderRadius: 12, padding: 34, textAlign: "center",
            color: "var(--ink4)", fontSize: 13, cursor: "pointer",
            background: dragOver ? "var(--ghost)" : "transparent",
          }}
        >
          Drop .json here or click to browse
          <input
            ref={fileRef} type="file" accept=".json,application/json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) processFile(f); }}
          />
        </div>
      )}

      {parseError && (
        <p style={{ color: "var(--bad)", fontSize: 12.5 }}>
          {parseError}{" "}
          <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => { setParseError(null); setFileName(null); }}>
            Try again
          </button>
        </p>
      )}

      {rows && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: "var(--ink3)" }}>
              <span className="mono">{rows.length}</span> prompts in <span className="mono">{fileName}</span>
            </span>
            {invalidCount > 0 && <span className="tag hi">{invalidCount} error{invalidCount !== 1 ? "s" : ""}</span>}
            <div style={{ flex: 1 }} />
            <button className="btn sm" onClick={() => { setRows(null); setFileName(null); }}>
              <ArrowBackRoundedIcon style={{ fontSize: 12 }} /> Change file
            </button>
          </div>
          <div style={{ maxHeight: 260, overflowY: "auto", border: "1px solid var(--b1)", borderRadius: 10 }}>
            <table className="tb">
              <thead>
                <tr><th style={{ width: 32 }}>#</th><th>Text</th><th style={{ width: 120 }}>Category</th></tr>
              </thead>
              <tbody>
                {rows.slice(0, 15).map((row) => (
                  <tr key={row.index}>
                    <td className="mono dim">{row.index + 1}</td>
                    <td style={{ fontSize: 12.5 }}>
                      {row.prompt.text || <span className="dim" style={{ fontStyle: "italic" }}>empty</span>}
                      {row.errors.length > 0 && (
                        <div style={{ color: "var(--bad)", fontSize: 10.5, marginTop: 2 }}>{row.errors.join("; ")}</div>
                      )}
                    </td>
                    <td style={{ fontSize: 11.5 }}>
                      {row.prompt.category
                        ? row.prompt.category
                        : row.unknownCategory
                          ? <span className="dim" title={`Unknown category "${row.rawCategory}" will import blank`}>{row.rawCategory} (blank)</span>
                          : <span className="dim">-</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && (
              <p style={{ textAlign: "center", fontSize: 11.5, color: "var(--ink4)", padding: "8px 0", borderTop: "1px solid var(--bf)" }}>
                ...and {rows.length - 15} more
              </p>
            )}
          </div>
        </>
      )}

      <div className="macts">
        <button className="btn" onClick={onClose}>Cancel</button>
        {rows && (
          <button
            className="btn pri"
            disabled={validRows.length === 0 || importMut.isPending}
            onClick={() => importMut.mutate(validRows.map((r) => r.prompt))}
          >
            {importMut.isPending ? "Importing..." : `Import ${validRows.length} prompt${validRows.length !== 1 ? "s" : ""}`}
          </button>
        )}
      </div>
      {importMut.isError && <p style={{ color: "var(--bad)", fontSize: 12 }}>Upload failed.</p>}
    </Modal>
  );
}

// ── Add / edit prompt modal ───────────────────────────────────────────────────

function PromptModal({ title, initialText, initialCat, categories, pending, error, onSave, onClose }: {
  title: string;
  initialText: string;
  initialCat: string;
  categories: string[];
  pending: boolean;
  error: string | null;
  onSave: (text: string, category: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(initialText);
  const [cat, setCat] = useState(initialCat);
  const dirty = text !== initialText || cat !== initialCat;

  return (
    <Modal onClose={onClose}>
      <h3>{title}</h3>
      <div className="ms">10 to 500 characters, phrased how a real buyer asks.</div>
      <div className="fld">
        <label>Prompt text *</label>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="best ..." maxLength={500} />
        <div className="fh" style={{ textAlign: "right" }}>{text.length}/500</div>
      </div>
      <div className="fld">
        <label>Category</label>
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">No category</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      {error && <p style={{ color: "var(--bad)", fontSize: 12.5 }}>{error}</p>}
      <div className="macts">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn pri" disabled={text.trim().length < 10 || pending || !dirty} onClick={() => onSave(text.trim(), cat)}>
          {pending ? "Saving..." : title}
        </button>
      </div>
    </Modal>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function ClientPrompts() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const toast = useToast();

  const [filterActive, setFilterActive] = useState<"true" | "false" | "">("true");
  const [rawSearch, setRawSearch] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showAdd, setShowAdd] = useState(false);
  const [showJsonUpload, setShowJsonUpload] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState<Prompt | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { setSearch(rawSearch); setPage(1); }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [rawSearch]);

  // Filtered query (table)
  const filters = {
    is_active: filterActive === "" ? undefined : filterActive === "true",
    search: search || undefined,
    page,
    per_page: 50,
  };
  const qKey = ["admin-prompts", clientId, filters] as const;
  const { data, isLoading } = useQuery({
    queryKey: qKey,
    queryFn: () => promptsApi.list(clientId!, filters),
    placeholderData: (prev) => prev,
  });

  // All prompts (for the KPI band)
  const { data: allData } = useQuery({
    queryKey: ["admin-prompts", clientId, "all"],
    queryFn: () => promptsApi.list(clientId!, { per_page: 200 }),
    enabled: !!clientId,
  });

  // Admin-configured categories (drive dropdowns, badges, filters).
  const { data: categories = [] } = useQuery({
    queryKey: ["prompt-categories"],
    queryFn: () => settingsApi.getPromptCategories(),
  });
  const categoryNames = categories.map((c) => c.name);
  const colorByName = useMemo(
    () => new Map(categories.map((c) => [c.name, c.color])),
    [categories],
  );

  // Latest run drives the per-prompt cite rates.
  const { data: runsList } = useQuery({
    queryKey: ["admin-runs", clientId, "prompts-latest"],
    queryFn: () => runsApi.list(clientId!, 1, 1),
    enabled: !!clientId,
  });
  const latestRun = runsList?.items[0];
  const { data: runPrompts } = useQuery({
    queryKey: ["admin-run-prompts", clientId, latestRun?.id],
    queryFn: () => runsApi.getPrompts(clientId!, latestRun!.id),
    enabled: !!clientId && !!latestRun?.id && ["completed", "partial"].includes(latestRun.status),
  });

  function invalidate() { qc.invalidateQueries({ queryKey: ["admin-prompts", clientId] }); }

  const createMut = useMutation({
    mutationFn: ({ text, category }: ParsedPrompt) => promptsApi.create(clientId!, text, category),
    onSuccess: () => { invalidate(); setShowAdd(false); setAddErr(null); toast("Prompt added"); },
    onError: () => setAddErr("Failed to add prompt (may already exist)"),
  });
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => promptsApi.update(clientId!, id, body),
    onSuccess: () => { invalidate(); setEditPrompt(null); toast("Prompt updated"); },
  });
  const toggleMut = useMutation<unknown, unknown, { id: string; active: boolean }, { prev: unknown }>({
    mutationFn: ({ id, active }) => active ? promptsApi.activate(clientId!, id) : promptsApi.deactivate(clientId!, id),
    onMutate: async ({ id, active }) => {
      await qc.cancelQueries({ queryKey: qKey });
      const prev = qc.getQueryData(qKey);
      qc.setQueryData(qKey, (old: typeof data) =>
        old ? { ...old, items: old.items.map((p) => (p.id === id ? { ...p, is_active: active } : p)) } : old);
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(qKey, ctx.prev); },
    onSettled: () => invalidate(),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / 50));

  // ── Stats ──
  const allPrompts = allData?.items ?? [];
  const totalAll = allData?.total ?? 0;
  const activeAll = allPrompts.filter((p) => p.is_active).length;
  const catCounts = categoryNames.reduce((acc, cat) => {
    acc[cat] = allPrompts.filter((p) => p.category === cat).length;
    return acc;
  }, {} as Record<string, number>);

  // Per-prompt cite rates from the latest run
  const promptCiteRates = new Map<string, number>();
  (runPrompts ?? []).forEach((pd) => {
    const cited = pd.results.filter((r) => r.client_cited === true).length;
    const rate = pd.results.length > 0 ? Math.round((cited / pd.results.length) * 100) : 0;
    promptCiteRates.set(pd.prompt_id, rate);
  });

  const kpiCols = Math.min(categories.length + 1, 7);

  return (
    <>
      <div className="cards" style={{ gridTemplateColumns: `repeat(${kpiCols}, 1fr)` }}>
        <div className="card">
          <div className="lbl">Total</div>
          <div className="val">{totalAll}</div>
          <div className="hint">{activeAll} active</div>
        </div>
        {categories.slice(0, kpiCols - 1).map((c) => (
          <div key={c.name} className="card">
            <div className="lbl"><span className="pd" style={{ background: c.color }} />{c.name}</div>
            <div className="val">{catCounts[c.name] ?? 0}</div>
            <div className="hint">{totalAll > 0 ? Math.round(((catCounts[c.name] ?? 0) / totalAll) * 100) : 0}% of library</div>
          </div>
        ))}
      </div>

      <div className="banner">
        <span className="bi dim"><InfoOutlinedIcon style={{ fontSize: 15 }} /></span>
        <div>
          <b>Prompt policy</b>
          <div className="note">
            Cap 100 per client, ~50 high-signal prompts recommended (16 Jul decision). Swapping prompts resets their trend, flag replacements to the client before saving.
          </div>
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <div style={{ display: "flex", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--bf)", alignItems: "center", flexWrap: "wrap" }}>
          <SearchBox value={rawSearch} onChange={setRawSearch} placeholder="Search prompts..." style={{ flex: 1 }} />
          <PillRow
            value={filterActive}
            onChange={(v) => { setFilterActive(v); setPage(1); }}
            options={[
              { value: "true" as const, label: "Active" },
              { value: "false" as const, label: "Inactive" },
              { value: "" as const, label: "All" },
            ]}
          />
          <button className="btn" onClick={() => { downloadJsonTemplate(categoryNames); toast("JSON template downloaded"); }}>
            <FileDownloadRoundedIcon style={{ fontSize: 14 }} /> Template
          </button>
          <button className="btn" onClick={() => setShowJsonUpload(true)}>
            <FileUploadRoundedIcon style={{ fontSize: 14 }} /> Upload JSON
          </button>
          <button className="btn pri" onClick={() => { setShowAdd(true); setAddErr(null); }}>
            <AddRoundedIcon style={{ fontSize: 15 }} /> Add prompt
          </button>
        </div>

        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : items.length === 0 ? (
          <EmptyState>No prompts found.</EmptyState>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th style={{ width: "52%" }}>Prompt</th>
                  <th>Category</th>
                  <th className="right">Cite rate</th>
                  <th>Active</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const citeRate = promptCiteRates.get(p.id);
                  return (
                    <tr key={p.id} style={!p.is_active ? { opacity: 0.5 } : undefined}>
                      <td style={{ fontSize: 13 }}>{p.text}</td>
                      <td><CategoryCell category={p.category} colorByName={colorByName} /></td>
                      <td className="right">
                        {citeRate != null ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
                            <span className="mono">{citeRate}%</span>
                            <BarMeter pct={citeRate} width={60} />
                          </span>
                        ) : (
                          <span className="dim">-</span>
                        )}
                      </td>
                      <td>
                        <TSwitch
                          on={p.is_active}
                          onToggle={() => toggleMut.mutate({ id: p.id, active: !p.is_active })}
                          label={p.is_active ? "Deactivate prompt" : "Activate prompt"}
                        />
                      </td>
                      <td className="right">
                        <button
                          className="iconb"
                          style={{ width: 26, height: 26 }}
                          aria-label="Edit prompt"
                          title="Edit prompt"
                          onClick={() => setEditPrompt(p)}
                        >
                          <EditRoundedIcon style={{ fontSize: 13 }} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderTop: "1px solid var(--bf)" }}>
            <button className="btn sm" disabled={page === 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <ChevronLeftRoundedIcon style={{ fontSize: 14 }} /> Prev
            </button>
            <span className="mono dim" style={{ fontSize: 11 }}>Page {page} of {totalPages}</span>
            <button className="btn sm" disabled={page === totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
              Next <ChevronRightRoundedIcon style={{ fontSize: 14 }} />
            </button>
          </div>
        )}
      </div>

      {showAdd && (
        <PromptModal
          title="Add prompt"
          initialText=""
          initialCat=""
          categories={categoryNames}
          pending={createMut.isPending}
          error={addErr}
          onSave={(text, category) => createMut.mutate({ text, category })}
          onClose={() => { setShowAdd(false); setAddErr(null); }}
        />
      )}

      {editPrompt && (
        <PromptModal
          title="Save changes"
          initialText={editPrompt.text}
          initialCat={editPrompt.category}
          categories={categoryNames}
          pending={updateMut.isPending}
          error={null}
          onSave={(text, category) => {
            const body: Record<string, unknown> = {};
            if (text !== editPrompt.text) body.text = text;
            if (category !== editPrompt.category) body.category = category;
            if (Object.keys(body).length) updateMut.mutate({ id: editPrompt.id, body });
            else setEditPrompt(null);
          }}
          onClose={() => setEditPrompt(null)}
        />
      )}

      {showJsonUpload && clientId && (
        <JsonUploadModal
          clientId={clientId}
          categories={categories}
          onClose={() => setShowJsonUpload(false)}
          onSuccess={(msg) => toast(msg)}
        />
      )}
    </>
  );
}
