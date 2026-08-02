import { useEffect, useState } from "react";
import { clientsApi } from "../../api/client";
import type { Client } from "../../types";
import { Modal } from "../ui/ui";

interface Props {
  onClose: () => void;
  onCreated: (client: Client) => void;
}

// Mirrors the backend's _slugify (api/app/api/admin_clients.py) exactly, so
// the live preview always matches what the server would derive.
function slugify(value: string): string {
  let slug = value.toLowerCase().trim();
  slug = slug.replace(/[^a-z0-9\s-]/g, "");
  slug = slug.replace(/\s+/g, "-");
  slug = slug.replace(/-+/g, "-").replace(/^-+|-+$/g, "");
  return slug.slice(0, 100);
}

type SlugStatus = "idle" | "checking" | "available" | "taken";

export function CreateClientModal({ onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [slugStatus, setSlugStatus] = useState<SlugStatus>("idle");
  const [industry, setIndustry] = useState("");
  const [website, setWebsite] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Auto-fill the slug from the name until the user edits the slug field
  // directly; clearing it by hand resumes auto-fill.
  useEffect(() => {
    if (!slugEdited) setSlug(slugify(name));
  }, [name, slugEdited]);

  // Debounced live availability check — every distinct slug the user could
  // submit is checked, so the Create button can be disabled before the
  // server ever sees a duplicate (it still enforces this on submit too).
  useEffect(() => {
    const trimmed = slug.trim();
    if (!trimmed) { setSlugStatus("idle"); return; }
    setSlugStatus("checking");
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const res = await clientsApi.checkSlug(trimmed);
        if (!cancelled) setSlugStatus(res.available ? "available" : "taken");
      } catch {
        // Fail open on a network hiccup — the create call is still guarded
        // server-side and will surface a 409 if the slug was actually taken.
        if (!cancelled) setSlugStatus("idle");
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [slug]);

  function handleSlugChange(raw: string) {
    setSlug(raw);
    setSlugEdited(raw.trim() !== "");
  }

  const slugValid = slug.trim().length > 0;
  const blocked = slugStatus === "checking" || slugStatus === "taken";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slugValid || blocked) return;
    setError(null);
    setLoading(true);
    try {
      const client = await clientsApi.create({
        name: name.trim(),
        slug: slug.trim(),
        industry: industry.trim() || undefined,
        website: website.trim() || undefined,
      });
      onCreated(client);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (typeof msg === "string" && msg.toLowerCase().includes("slug")) {
        setSlugStatus("taken");
        setError(msg);
      } else {
        setError(typeof msg === "string" ? msg : "Failed to create client");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal onClose={onClose}>
      <h3>New client</h3>
      <div className="ms">Creates the client shell, prompts, KB and competitors come from the pre-audit import.</div>
      <form onSubmit={handleSubmit}>
        <div className="fld">
          <label>Client name *</label>
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Pty Ltd" />
        </div>
        <div className="fld">
          <label>Slug *</label>
          <input
            type="text"
            required
            value={slug}
            onChange={(e) => handleSlugChange(e.target.value)}
            placeholder="acme-pty-ltd"
            style={{ fontFamily: "var(--mono)" }}
          />
          <div
            className="fh"
            style={slugStatus === "taken" ? { color: "var(--bad)" } : slugStatus === "available" ? { color: "var(--good)" } : undefined}
          >
            {!slugValid
              ? "Used in the client's dashboard URL, names can repeat but slugs must be unique."
              : slugStatus === "checking"
                ? "Checking availability..."
                : slugStatus === "taken"
                  ? "This slug is already used by another client, change it to continue."
                  : slugStatus === "available"
                    ? "Available."
                    : "Used in the client's dashboard URL, names can repeat but slugs must be unique."}
          </div>
        </div>
        <div className="fld">
          <label>Industry</label>
          <input type="text" value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="B2B SaaS" />
        </div>
        <div className="fld">
          <label>Website</label>
          <input type="url" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://acme.com" />
        </div>

        {error && <p style={{ color: "var(--bad)", fontSize: 12.5, marginBottom: 8 }}>{error}</p>}

        <div className="macts">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn pri" disabled={loading || !name.trim() || !slugValid || blocked}>
            {loading ? "Creating..." : "Create client"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
