import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { AuthGuard } from "./auth/AuthGuard";
import { LoginPage } from "./auth/LoginPage";
import { ChangePasswordPage } from "./auth/ChangePasswordPage";
import { DashboardLayout } from "./components/DashboardLayout";
import { DashboardHome } from "./components/DashboardHome";
import { RunDetailPage } from "./components/RunDetailPage";
import { RunHistoryPage } from "./components/RunHistoryPage";
import { RecommendationsPage } from "./components/RecommendationsPage";

// Sections the admin has hidden for this client are not reachable — a deep link
// into a disabled view falls back to the dashboard, mirroring the nav.
function RequireDisplay({ flag, children }: { flag: string; children: React.ReactNode }) {
  const { display } = useAuth();
  if (!display[flag]) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/change-password" element={<ChangePasswordPage />} />

        <Route
          path="/dashboard"
          element={
            <AuthGuard>
              <DashboardLayout />
            </AuthGuard>
          }
        >
          <Route index element={<DashboardHome />} />
          <Route path="runs" element={<RequireDisplay flag="runs"><RunHistoryPage /></RequireDisplay>} />
          <Route path="runs/:runId" element={<RequireDisplay flag="runs"><RunDetailPage /></RequireDisplay>} />
          <Route path="recommendations" element={<RequireDisplay flag="recs"><RecommendationsPage /></RequireDisplay>} />
          <Route path="recommendations/:recId" element={<RequireDisplay flag="recs"><RecommendationsPage /></RequireDisplay>} />
        </Route>

        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
