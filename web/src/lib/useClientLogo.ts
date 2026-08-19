import { useEffect, useState } from "react";
import { dashboard } from "./api";

/**
 * The object URL for this client's brand logo, or null when they have none.
 *
 * The logo endpoint is authenticated, so the bytes are fetched with the bearer
 * token and turned into an object URL for <img src>. `updatedAt` is the cache
 * key: when an admin replaces the logo the timestamp changes and the hook
 * re-fetches, and the previous URL is revoked so the blob is not leaked.
 */
export function useClientLogo(hasLogo: boolean | undefined, updatedAt: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!hasLogo) {
      setUrl(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;

    dashboard
      .getLogo()
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      // A missing or unreadable logo is not worth an error state: the header
      // falls back to the Citiq mark alone.
      .catch(() => setUrl(null));

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [hasLogo, updatedAt]);

  return url;
}
