import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { resolveDisplayConfig, type DisplayConfig } from "../lib/display";

export interface ClientUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  client_id: string;
  client_name: string;
  must_change_password: boolean;
  // Effective client-display flags (resolved server-side). Absent on older
  // token payloads — callers read `display` off the context instead, which
  // always resolves to a complete set.
  display_config?: DisplayConfig;
}

interface AuthState {
  user: ClientUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  mustChangePassword: boolean;
  // Effective client-display flags, always a complete set (defaults applied).
  display: DisplayConfig;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const API = import.meta.env.VITE_API_URL ?? "";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem("client_access_token");
  const res = await fetch(`${API}${path}`, {
    // Never serve /me (display config) from the HTTP cache — it must reflect the
    // admin's latest change, not a stale 200.
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function tryRefresh(): Promise<boolean> {
  const refresh = localStorage.getItem("client_refresh_token");
  if (!refresh) return false;
  try {
    const data = await apiFetch<{ access_token: string }>("/client/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refresh }),
    });
    localStorage.setItem("client_access_token", data.access_token);
    return true;
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<ClientUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("client_access_token");
    if (!token) { setIsLoading(false); return; }

    apiFetch<ClientUser>("/client/auth/me")
      .then((u) => setUser(u))
      .catch(async () => {
        const refreshed = await tryRefresh();
        if (refreshed) {
          try { setUser(await apiFetch<ClientUser>("/client/auth/me")); }
          catch { localStorage.clear(); }
        } else {
          localStorage.removeItem("client_access_token");
          localStorage.removeItem("client_refresh_token");
        }
      })
      .finally(() => setIsLoading(false));
  }, []);

  // Keep the profile — and with it the display config — live. An admin can
  // change what this client sees at any time, so re-fetch /me on every trigger
  // that means "the user might act on the app": tab focus, in-app navigation,
  // and a slow fallback interval. The nav links and route guards read `display`
  // reactively, so a section the admin just hid vanishes from the nav and the
  // guard redirects the user off it, with no manual refresh. Failures are
  // ignored on purpose: the mount effect above owns auth refresh / logout.
  const authed = user !== null;
  const location = useLocation();

  const refreshMe = useCallback(() => {
    if (!localStorage.getItem("client_access_token")) return;
    apiFetch<ClientUser>("/client/auth/me")
      .then(setUser)
      .catch(() => { /* keep the last known config until the next attempt */ });
  }, []);

  // Re-check whenever the client navigates between sections — this is what makes
  // clicking a just-removed tab bounce back to the dashboard.
  useEffect(() => {
    if (authed) refreshMe();
  }, [authed, location.pathname, refreshMe]);

  // Re-check on tab focus (admin toggles in one tab, views the client in
  // another) and on a fallback interval for a client left sitting on a page.
  useEffect(() => {
    if (!authed) return;
    const onVisible = () => { if (document.visibilityState === "visible") refreshMe(); };
    const id = window.setInterval(refreshMe, 20_000);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [authed, refreshMe]);

  async function login(email: string, password: string) {
    const data = await apiFetch<{
      access_token: string;
      refresh_token: string;
      user: ClientUser;
      must_change_password: boolean;
    }>("/client/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("client_access_token", data.access_token);
    localStorage.setItem("client_refresh_token", data.refresh_token);
    setUser({ ...data.user, must_change_password: data.must_change_password });
  }

  function logout() {
    localStorage.removeItem("client_access_token");
    localStorage.removeItem("client_refresh_token");
    setUser(null);
  }

  const display = useMemo(() => resolveDisplayConfig(user?.display_config), [user?.display_config]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        mustChangePassword: user?.must_change_password ?? false,
        display,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
