import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./pages/Login";
import { ApplicationsListPage } from "./pages/ApplicationsList";
import { ApplicationDetailPage } from "./pages/ApplicationDetail";
import { ClientsListPage } from "./pages/ClientsList";
import { ClientDetailPage } from "./pages/ClientDetail";
import { ContractsListPage } from "./pages/ContractsList";
import { ContractDetailPage } from "./pages/ContractDetail";
import { ReferencesPage } from "./pages/References";
import { EmployeesPage } from "./pages/Employees";
import { AuditLogPage } from "./pages/AuditLog";
import { SystemStatusPage } from "./pages/SystemStatus";
import { CompliancePage } from "./pages/Compliance";
import { DashboardPage } from "./pages/Dashboard";
import { RulesetsPage } from "./pages/Rulesets";
import { ImportsPage } from "./pages/Imports";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/applications" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/applications" element={<ApplicationsListPage />} />
          <Route path="/applications/:id" element={<ApplicationDetailPage />} />
          <Route path="/clients" element={<ClientsListPage />} />
          <Route path="/clients/:id" element={<ClientDetailPage />} />
          <Route path="/contracts" element={<ContractsListPage />} />
          <Route path="/contracts/:id" element={<ContractDetailPage />} />
          <Route path="/references" element={<ReferencesPage />} />
          <Route path="/employees" element={<EmployeesPage />} />
          <Route path="/audit" element={<AuditLogPage />} />
          <Route path="/system" element={<SystemStatusPage />} />
          <Route path="/compliance" element={<CompliancePage />} />
          <Route path="/rulesets" element={<RulesetsPage />} />
          <Route path="/imports" element={<ImportsPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
