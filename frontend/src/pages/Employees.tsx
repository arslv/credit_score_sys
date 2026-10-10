import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge } from "../components/Badge";

const ROLES = ["risk_manager", "admin", "viewer", "compliance", "operator"];

export function EmployeesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ full_name: "", email: "", role: "viewer", password: "" });
  const [formError, setFormError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["employees"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/employees", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/employees", { body: form });
      if (error) throw new Error(typeof error.detail === "string" ? error.detail : "не удалось создать");
    },
    onSuccess: () => {
      setForm({ full_name: "", email: "", role: "viewer", password: "" });
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const updateMutation = useMutation({
    mutationFn: async (input: { id: string; patch: { active?: boolean; role?: string } }) => {
      const { error } = await api.PATCH("/api/employees/{employee_id}", {
        params: { path: { employee_id: input.id } },
        body: input.patch,
      });
      if (error) throw new Error(typeof error.detail === "string" ? error.detail : "не удалось изменить");
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["employees"] }),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    createMutation.mutate();
  }

  return (
    <section>
      <h1>Сотрудники</h1>

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить список сотрудников.</p>}
      {query.data && (
        <table className="table">
          <thead>
            <tr>
              <th>ФИО</th>
              <th>Email</th>
              <th>Роль</th>
              <th>Активен</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((e) => (
              <tr key={e.id}>
                <td>{e.full_name}</td>
                <td className="muted">{e.email}</td>
                <td>
                  <select
                    value={e.role}
                    onChange={(ev) => updateMutation.mutate({ id: e.id, patch: { role: ev.target.value } })}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {/* Найденный баг (T17, аудит): раньше здесь был чужой код статуса
                      заявки ("ok"/"rejected_a") только ради цвета — показывал
                      буквально слово "rejected_a" для неактивного сотрудника.
                      Своя пара значений, явная подпись. */}
                  <Badge value={e.active ? "employee_active" : "employee_inactive"} label={e.active ? "активен" : "неактивен"} />
                </td>
                <td>
                  <button
                    className="btn btn-small"
                    onClick={() => updateMutation.mutate({ id: e.id, patch: { active: !e.active } })}
                  >
                    {e.active ? "деактивировать" : "активировать"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {updateMutation.isError && (
        <p role="alert" className="alert">
          {(updateMutation.error as Error).message}
        </p>
      )}

      <h2>Новый сотрудник</h2>
      <form onSubmit={handleSubmit} className="card">
        <label className="field">
          ФИО
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
        </label>
        <label className="field">
          Email
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            required
          />
        </label>
        <label className="field">
          Роль
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Пароль
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
        </label>
        {formError && <p role="alert" className="alert">{formError}</p>}
        <button type="submit" className="btn btn-primary" disabled={createMutation.isPending}>
          Создать
        </button>
      </form>
    </section>
  );
}
