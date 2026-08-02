import { Routes, Route, Navigate } from "react-router-dom";
import { AuthGuard } from "./auth/AuthGuard";
import { LoginPage } from "./auth/LoginPage";
import { AdminLayout } from "./components/layout/AdminLayout";
import { ClientList } from "./components/clients/ClientList";
import { ClientDetail } from "./components/clients/ClientDetail";
import { ClientOverview } from "./components/clients/ClientOverview";
import { ClientPrompts } from "./components/clients/ClientPrompts";
import { ClientCompetitors } from "./components/clients/ClientCompetitors";
import { ClientKnowledgeBase } from "./components/clients/ClientKnowledgeBase";
import { ClientRuns } from "./components/clients/ClientRuns";
import { ClientRecommendations } from "./components/clients/ClientRecommendations";
import { ClientSchedule } from "./components/clients/ClientSchedule";
import { ClientSettings } from "./components/clients/ClientSettings";
import { ClientUsers } from "./components/clients/ClientUsers";
import { RunDetail } from "./components/clients/RunDetail";
import { SchedulerHealth } from "./components/scheduler/SchedulerHealth";
import { RecommendationDetailPage } from "./components/recommendations/RecommendationDetail";
import { GlobalSettings } from "./components/settings/GlobalSettings";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <AuthGuard>
            <AdminLayout />
          </AuthGuard>
        }
      >
        <Route index element={<Navigate to="/clients" replace />} />
        <Route path="/clients" element={<ClientList />} />

        <Route path="/clients/:clientId" element={<ClientDetail />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<ClientOverview />} />
          <Route path="prompts" element={<ClientPrompts />} />
          <Route path="competitors" element={<ClientCompetitors />} />
          <Route path="knowledge-base" element={<ClientKnowledgeBase />} />
          <Route path="runs" element={<ClientRuns />} />
          <Route path="recommendations" element={<ClientRecommendations />} />
          <Route path="schedule" element={<ClientSchedule />} />
          <Route path="users" element={<ClientUsers />} />
          <Route path="settings" element={<ClientSettings />} />
        </Route>

        {/* RunDetail is outside ClientDetail tabs — full-page layout */}
        <Route path="/clients/:clientId/runs/:runId" element={<RunDetail />} />

        {/* Global scheduler health page */}
        <Route path="/scheduler" element={<SchedulerHealth />} />

        {/* Recommendations are reviewed inside each client (16 Jul redesign);
            the old global queue URL redirects, deep links to a single
            recommendation still resolve. */}
        <Route path="/recommendations" element={<Navigate to="/clients" replace />} />
        <Route path="/recommendations/:id" element={<RecommendationDetailPage />} />

        {/* Global (system-wide) settings */}
        <Route path="/settings" element={<GlobalSettings />} />
      </Route>

      <Route path="*" element={<Navigate to="/clients" replace />} />
    </Routes>
  );
}
