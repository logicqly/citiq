import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { OrigoMark } from "../components/ui";

const API = import.meta.env.VITE_API_URL ?? "";

export function ChangePasswordPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (next.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setError("Passwords do not match."); return; }

    setLoading(true);
    try {
      const token = localStorage.getItem("client_access_token");
      const res = await fetch(`${API}/client/auth/change-password`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail ?? "Failed to change password");
      }

      // Re-login to get a fresh token without must_change_password = true
      navigate("/login", { replace: true });
      logout();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login">
      <div className="lcard">
        <div style={{ width: 40, margin: "0 auto 16px", color: "var(--white)" }}>
          <OrigoMark size={40} />
        </div>
        <h1>Change password</h1>
        <div className="ls">
          Welcome{user?.display_name ? `, ${user.display_name}` : ""}. Minimum 8 characters.
        </div>

        <form onSubmit={handleSubmit}>
          <div className="fld">
            <label>Current password</label>
            <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
          </div>
          <div className="fld">
            <label>New password</label>
            <input type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div className="fld">
            <label>Confirm new password</label>
            <input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>

          {error && <p style={{ color: "var(--bad)", fontSize: 13, marginBottom: 12 }}>{error}</p>}

          <button
            type="submit"
            disabled={loading || !current || !next || !confirm}
            className="btn pri"
            style={{ width: "100%", justifyContent: "center", marginTop: 6 }}
          >
            {loading ? "Saving..." : "Update password"}
          </button>
        </form>
      </div>
    </div>
  );
}
