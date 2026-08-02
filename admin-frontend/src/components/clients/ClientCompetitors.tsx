import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import { clientsApi, competitorsApi, runsApi } from "../../api/client";
import { HBars } from "../ui/charts";
import { EmptyState, Modal, pctFmt, useConfirm, useToast } from "../ui/ui";

export function ClientCompetitors() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();

  const [newName, setNewName] = useState("");
  const [bulkText, setBulkText] = useState("");
  const [showBulk, setShowBulk] = useState(false);

  const { data: client } = useQuery({
    queryKey: ["admin-client", clientId],
    queryFn: () => clientsApi.get(clientId!),
    enabled: !!clientId,
  });

  const { data: competitors = [], isLoading } = useQuery({
    queryKey: ["admin-competitors", clientId],
    queryFn: () => competitorsApi.list(clientId!),
    enabled: !!clientId,
  });

  // Latest run feeds share of voice and the suggestions.
  const { data: runsList } = useQuery({
    queryKey: ["admin-runs", clientId, "competitors-latest"],
    queryFn: () => runsApi.list(clientId!, 1, 1),
    enabled: !!clientId,
  });
  const latestRun = runsList?.items[0];
  const { data: latestRunSummary } = useQuery({
    queryKey: ["admin-run-detail", clientId, latestRun?.id],
    queryFn: () => runsApi.get(clientId!, latestRun!.id),
    enabled: !!clientId && !!latestRun?.id && ["completed", "partial"].includes(latestRun?.status ?? ""),
  });

  const stats = latestRunSummary?.competitor_stats ?? [];
  const totalSeen = stats.reduce((s, c) => s + c.cited_count, 0);
  const clientName = (client?.name ?? "").toLowerCase();
  const seenByBrand = new Map(stats.map((c) => [c.brand.toLowerCase(), c.cited_count]));

  const trackedNames = new Set(competitors.map((c) => c.name.toLowerCase()));
  const suggested = stats
    .filter((c) => !trackedNames.has(c.brand.toLowerCase()) && c.brand.toLowerCase() !== clientName)
    .sort((a, b) => b.cited_count - a.cited_count)
    .slice(0, 6);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-competitors", clientId] });

  const createMut = useMutation({
    mutationFn: (name: string) => competitorsApi.create(clientId!, name),
    onSuccess: (_d, name) => { invalidate(); setNewName(""); toast(`${name} added`); },
    onError: () => toast("Failed to add competitor (may already exist)", "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => competitorsApi.delete(clientId!, id),
    onSuccess: () => { invalidate(); toast("Competitor removed"); },
  });

  const bulkMut = useMutation({
    mutationFn: (names: string[]) => competitorsApi.bulkCreate(clientId!, names),
    onSuccess: (res) => {
      invalidate(); setBulkText(""); setShowBulk(false);
      toast(`Added ${res.created}, skipped ${res.skipped} duplicates`);
    },
  });

  function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    createMut.mutate(newName.trim());
  }

  async function handleRemove(id: string, name: string) {
    const ok = await confirm({
      title: "Remove competitor?",
      message: `${name} will no longer be scored against this client on future runs.`,
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok) deleteMut.mutate(id);
  }

  return (
    <>
      <div className="grid31">
        <div className="panel">
          <div className="ph">
            <h3>Share of voice</h3>
            <span className="note">latest run, {totalSeen} competitor citations</span>
          </div>
          {stats.length > 0 ? (
            <HBars
              rows={stats.map((c) => ({
                label: c.brand,
                v: c.share_of_voice,
                right: `${pctFmt(c.share_of_voice)}, ${c.cited_count}`,
                self: c.brand.toLowerCase() === clientName,
              }))}
            />
          ) : (
            <EmptyState>
              {competitors.length < 3
                ? `Add at least 3 competitors to unlock share-of-voice scoring (${competitors.length} tracked).`
                : "No competitor citations in the latest run yet."}
            </EmptyState>
          )}
        </div>

        <div>
          <div className="panel">
            <div className="ph">
              <h3>Tracked competitors</h3>
              <span className="note">{competitors.length}</span>
              <div className="sp" />
              <button className="btn sm" onClick={() => setShowBulk(true)}>Bulk add</button>
            </div>
            {isLoading ? (
              <EmptyState>Loading...</EmptyState>
            ) : competitors.length === 0 ? (
              <EmptyState>No competitors yet. Add at least 3 to unlock share-of-voice scoring.</EmptyState>
            ) : (
              competitors.map((c) => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", padding: "7px 0", borderBottom: "1px solid var(--bf)", fontSize: 13 }}>
                  {c.name}
                  <span className="mono dim" style={{ marginLeft: "auto", fontSize: 11 }}>
                    {seenByBrand.has(c.name.toLowerCase()) ? `seen ${seenByBrand.get(c.name.toLowerCase())}x` : "-"}
                  </span>
                  <button
                    className="dim"
                    style={{ marginLeft: 12, background: "none", border: "none", display: "inline-flex", padding: 0, color: "var(--ink4)" }}
                    aria-label={`Remove ${c.name}`}
                    title={`Remove ${c.name}`}
                    onClick={() => handleRemove(c.id, c.name)}
                  >
                    <CloseRoundedIcon style={{ fontSize: 14 }} />
                  </button>
                </div>
              ))
            )}
            <form onSubmit={handleAdd} style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Add competitor..."
                style={{
                  flex: 1, background: "var(--s4)", border: "1px solid var(--b1)", borderRadius: 8,
                  color: "var(--ink1)", padding: "8px 10px", fontSize: 13, outline: "none", minWidth: 0,
                }}
              />
              <button type="submit" className="btn sm pri" disabled={!newName.trim() || createMut.isPending}>
                Add
              </button>
            </form>
          </div>

          <div className="panel">
            <div className="ph"><h3>Suggested from runs</h3></div>
            {suggested.length > 0 ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {suggested.map((s) => (
                  <button
                    key={s.brand}
                    className="chip"
                    style={{ cursor: "pointer", background: "none" }}
                    onClick={() => createMut.mutate(s.brand)}
                  >
                    <AddRoundedIcon style={{ fontSize: 11 }} /> {s.brand}
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState>Suggestions appear after the next run.</EmptyState>
            )}
            <div className="footer-note">surfaced from competitor_stats of the latest run, no hardcoded list</div>
          </div>
        </div>
      </div>

      {showBulk && (
        <Modal onClose={() => setShowBulk(false)}>
          <h3>Bulk add competitors</h3>
          <div className="ms">One name per line, duplicates are skipped.</div>
          <div className="fld">
            <label>Competitor names</label>
            <textarea
              rows={5}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={"BambooHR\nRippling\nHiBob"}
            />
          </div>
          <div className="macts">
            <button className="btn" onClick={() => setShowBulk(false)}>Cancel</button>
            <button
              className="btn pri"
              disabled={!bulkText.trim() || bulkMut.isPending}
              onClick={() => {
                const names = bulkText.split("\n").map((l) => l.trim()).filter(Boolean);
                if (names.length) bulkMut.mutate(names);
              }}
            >
              {bulkMut.isPending ? "Adding..." : "Add all"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
