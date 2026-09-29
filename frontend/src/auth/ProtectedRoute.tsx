import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthProvider";

export function ProtectedRoute() {
  const { employee, loading } = useAuth();
  if (loading) return <p>Загрузка…</p>;
  if (!employee) return <Navigate to="/login" replace />;
  return <Outlet />;
}
