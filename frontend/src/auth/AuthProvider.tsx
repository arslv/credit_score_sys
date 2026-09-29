import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import { queryClient } from "../lib/queryClient";
import type { components } from "../api/schema";

type Employee = components["schemas"]["EmployeeOut"];

interface AuthState {
  employee: Employee | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<{ ok: true } | { ok: false; error: string }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [loading, setLoading] = useState(true);

  const clearSession = () => {
    setEmployee(null);
    // Персданные заявок — только в памяти, при выходе/401 кэш обязан
    // очиститься полностью (ваше уточнение).
    queryClient.clear();
  };

  useEffect(() => {
    setUnauthorizedHandler(clearSession);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.GET("/api/auth/me").then(({ data }) => {
      if (!cancelled && data) setEmployee(data);
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const login: AuthState["login"] = async (email, password) => {
    const { data, error, response } = await api.POST("/api/auth/login", {
      body: { email, password },
    });
    if (error || !data) {
      if (response.status === 429) {
        return { ok: false, error: "Слишком много попыток входа, попробуйте позже" };
      }
      return { ok: false, error: "Неверный email или пароль" };
    }
    setEmployee(data);
    return { ok: true };
  };

  const logout: AuthState["logout"] = async () => {
    await api.POST("/api/auth/logout", {});
    clearSession();
  };

  return (
    <AuthContext.Provider value={{ employee, loading, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth должен использоваться внутри AuthProvider");
  return ctx;
}
