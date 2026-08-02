import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { authApi } from "../api/client";
import { useAuth } from "./AuthContext";
import { OrigoMark } from "../components/ui/mark";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? "/clients";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await authApi.login(email, password);
      login(res);
      navigate(from, { replace: true });
    } catch {
      setError("Invalid email or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login">
      <div className="lcard">
        <div className="lm" style={{ width: 44, margin: "0 auto 16px" }}>
          <OrigoMark size={44} />
        </div>
        <h1>Origo Admin</h1>
        <div className="ls">Be the <i>source</i>.</div>

        <form onSubmit={handleSubmit}>
          <div className="fld">
            <label>Email</label>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@origolabs.ai"
            />
          </div>
          <div className="fld">
            <label>Password</label>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && (
            <p style={{ color: "var(--bad)", fontSize: 13, marginBottom: 12 }}>{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn pri"
            style={{ width: "100%", justifyContent: "center", marginTop: 6 }}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <div className="footer-note" style={{ textAlign: "center" }}>POST /admin/auth/login</div>
      </div>
    </div>
  );
}
