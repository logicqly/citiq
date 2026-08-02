import { useState } from "react";
import { Outlet, useNavigate, NavLink, useLocation } from "react-router-dom";
import DarkModeRoundedIcon from "@mui/icons-material/DarkModeRounded";
import LightModeRoundedIcon from "@mui/icons-material/LightModeRounded";
import KeyboardArrowDownRoundedIcon from "@mui/icons-material/KeyboardArrowDownRounded";
import { useAuth } from "../auth/AuthContext";
import { useTheme } from "../lib/theme";
import { OrigoMark } from "./ui";

export function DashboardLayout() {
  const { user, logout, display } = useAuth();
  const { dark, toggle: toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  const initials = (user?.display_name ?? "U")
    .split(/\s+/).map((w) => w[0]).join("").toUpperCase().slice(0, 2);

  return (
    <div style={{ minHeight: "100vh" }}>
      <header className="top">
        <NavLink to="/dashboard" className="brand" end>
          <OrigoMark size={24} />
          <div className="hidden sm:block">
            <div className="t1">GEO MONITOR</div>
            <div className="t2">{user?.client_name}</div>
          </div>
        </NavLink>

        <nav className="nav">
          <NavLink to="/dashboard" end className={({ isActive }) => (isActive ? "on" : "")}>
            Dashboard
          </NavLink>
          {display.runs && (
            <NavLink to="/dashboard/runs" className={({ isActive }) => (isActive ? "on" : "")}>
              Run history
            </NavLink>
          )}
          {display.recs && (
            <NavLink to="/dashboard/recommendations" className={({ isActive }) => (isActive ? "on" : "")}>
              Recommendations
            </NavLink>
          )}
        </nav>

        <div className="sp" />

        <button className="iconb" onClick={toggleTheme} title="Toggle theme" aria-label="Toggle light / dark theme">
          {dark ? <DarkModeRoundedIcon style={{ fontSize: 15 }} /> : <LightModeRoundedIcon style={{ fontSize: 15 }} />}
        </button>

        <div
          className="userm"
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setMenuOpen((v) => !v); }}
        >
          <div className="av">{initials}</div>
          <span className="nm hidden sm:block">{user?.display_name}</span>
          <span className="dim" style={{ display: "inline-flex" }}>
            <KeyboardArrowDownRoundedIcon style={{ fontSize: 14 }} />
          </span>
          <div className={`umenu${menuOpen ? " open" : ""}`} onClick={(e) => e.stopPropagation()}>
            <button onClick={() => { setMenuOpen(false); navigate("/change-password"); }}>
              Change password
            </button>
            <button onClick={handleLogout}>Sign out</button>
          </div>
        </div>
        {menuOpen && (
          <div
            style={{ position: "fixed", inset: 0, zIndex: 20 }}
            onClick={() => setMenuOpen(false)}
          />
        )}
      </header>

      <div className="wrap" key={location.pathname}>
        <Outlet />
      </div>
    </div>
  );
}
