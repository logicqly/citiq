import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import VisibilityRoundedIcon from "@mui/icons-material/VisibilityRounded";
import VisibilityOffRoundedIcon from "@mui/icons-material/VisibilityOffRounded";
import { useAuth } from "./AuthContext";
import { OrigoMark } from "../components/ui";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? "/dashboard";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("429")) {
        setError("Too many login attempts. Please wait 15 minutes.");
      } else if (msg.includes("403")) {
        setError("Your account is inactive. Contact your administrator.");
      } else {
        setError("Invalid email or password.");
      }
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
        <h1>GEO Monitor</h1>
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
              placeholder="you@company.com"
            />
          </div>
          <div className="fld">
            <label>Password</label>
            <div style={{ position: "relative" }}>
              <input
                type={showPw ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{ paddingRight: 36 }}
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                aria-label={showPw ? "Hide password" : "Show password"}
                style={{
                  position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
                  background: "none", border: "none", color: "var(--ink4)", display: "inline-flex", padding: 0,
                }}
              >
                {showPw ? <VisibilityOffRoundedIcon style={{ fontSize: 15 }} /> : <VisibilityRoundedIcon style={{ fontSize: 15 }} />}
              </button>
            </div>
          </div>

          {error && <p style={{ color: "var(--bad)", fontSize: 13, marginBottom: 12 }}>{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="btn pri"
            style={{ width: "100%", justifyContent: "center", marginTop: 6 }}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <div className="footer-note" style={{ textAlign: "center" }}>Engineered from origin, origolabs.ai</div>
      </div>
    </div>
  );
}
