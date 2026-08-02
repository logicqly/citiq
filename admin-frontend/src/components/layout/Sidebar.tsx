import { NavLink, useNavigate } from "react-router-dom";
import PeopleAltRoundedIcon from "@mui/icons-material/PeopleAltRounded";
import ScheduleRoundedIcon from "@mui/icons-material/ScheduleRounded";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";
import { useAuth } from "../../auth/AuthContext";
import { OrigoMark } from "../ui/mark";
import { getInitials } from "../ui/ui";
import { usePendingReview } from "../ui/usePendingReview";

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { clients, total, clientsWithPending } = usePendingReview();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  const initials = user?.display_name ? getInitials(user.display_name) : "AD";

  const navCls = ({ isActive }: { isActive: boolean }) => `nav-it${isActive ? " on" : ""}`;

  return (
    <aside className={`side${open ? " open" : ""}`}>
      <div className="brand">
        <OrigoMark size={25} />
        <div className="wm">Origo <span>Labs</span></div>
        <span className="env">ADMIN</span>
      </div>

      <div className="nav-sec">Navigation</div>
      <NavLink to="/clients" className={navCls} onClick={onClose}>
        <span className="ic"><PeopleAltRoundedIcon style={{ fontSize: 16 }} /></span>
        Clients
        {clients.length > 0 && <span className="bdg">{clients.length}</span>}
      </NavLink>
      <NavLink to="/scheduler" className={navCls} onClick={onClose}>
        <span className="ic"><ScheduleRoundedIcon style={{ fontSize: 16 }} /></span>
        Scheduler
      </NavLink>

      <div className="nav-sec">System</div>
      <NavLink to="/settings" className={navCls} onClick={onClose}>
        <span className="ic"><SettingsRoundedIcon style={{ fontSize: 16 }} /></span>
        Settings
      </NavLink>

      <div className="side-card">
        <div className="sc-l">Review queue</div>
        <div className="sc-n">{total}</div>
        <div className="sc-h">
          {total > 0
            ? `pending across ${clientsWithPending} client${clientsWithPending !== 1 ? "s" : ""}, review inside each client`
            : "review queue is clear"}
        </div>
      </div>

      <div className="foot">
        <div className="av">{initials}</div>
        <div className="who">
          <b>{user?.display_name ?? "Admin"}</b>
          <span>{user?.email}</span>
        </div>
        <button className="out" onClick={handleLogout} title="Sign out" aria-label="Sign out">
          <LogoutRoundedIcon style={{ fontSize: 15 }} />
        </button>
      </div>
    </aside>
  );
}
