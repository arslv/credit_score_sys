import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  SearchInput,
  Toolbar,
  matches,
} from "../components/ui";
import { EMPLOYEE_ROLES, roleLabel } from "../lib/labels";

interface EmployeeRow {
  id: string;
  full_name: string;
  email: string;
  role: string;
  active: boolean;
}

const EMPTY_FORM = { full_name: "", email: "", role: "operator", password: "" };

function errorText(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : fallback;
}

// T23: создание и редактирование — в модальном окне; в таблице роль и
// статус — подписями, без выпадающих списков прямо в строках.
export function EmployeesPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<EmployeeRow | null>(null);

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

  const rows = (query.data?.items ?? []).filter(
    (e) => (!roleFilter || e.role === roleFilter) && matches(search, e.full_name, e.email),
  );

  return (
    <section>
      <PageHeader
        title="Сотрудники"
        description="Учётные записи админки: кто входит и с какой ролью. Роль определяет, что сотрудник видит и меняет."
        actions={
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            Новый сотрудник
          </button>
        }
      />

      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Имя или email" />
        <select className="select" aria-label="Роль" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
          <option value="">Все роли</option>
          {EMPLOYEE_ROLES.map((r) => (
            <option key={r} value={r}>
              {roleLabel(r)}
            </option>
          ))}
        </select>
        {query.data && (
          <span className="toolbar__spacer toolbar__count">
            {rows.length} из {query.data.items.length}
          </span>
        )}
      </Toolbar>

      {query.isLoading && <LoadingState />}
      {query.isError && (
        <ErrorState message="Не удалось загрузить список сотрудников." onRetry={() => void query.refetch()} />
      )}
      {query.data && rows.length === 0 && (
        <EmptyState
          title={query.data.items.length === 0 ? "Сотрудников пока нет" : "Никого не нашлось"}
          hint={query.data.items.length === 0 ? undefined : "Измените поиск или фильтр по роли."}
          action={
            query.data.items.length === 0 ? (
              <button className="btn btn-primary" onClick={() => setCreating(true)}>
                Новый сотрудник
              </button>
            ) : undefined
          }
        />
      )}
      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>ФИО</th>
              <th>Email</th>
              <th>Роль</th>
              <th>Статус</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td>{e.full_name}</td>
                <td className="muted">{e.email}</td>
                <td>{roleLabel(e.role)}</td>
                <td>
                  {/* Найденный баг (T17, аудит): раньше здесь был чужой код статуса
                      заявки — своя пара значений, явная подпись. */}
                  <Badge value={e.active ? "employee_active" : "employee_inactive"} label={e.active ? "активен" : "отключён"} />
                </td>
                <td className="cell-actions">
                  <button className="btn btn-small" onClick={() => setEditing(e)}>
                    Изменить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <CreateEmployeeModal
        open={creating}
        onClose={() => setCreating(false)}
        onDone={() => {
          setCreating(false);
          void queryClient.invalidateQueries({ queryKey: ["employees"] });
        }}
      />
      {editing && (
        <EditEmployeeModal
          employee={editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["employees"] });
          }}
        />
      )}
    </section>
  );
}

function RoleSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {EMPLOYEE_ROLES.map((r) => (
        <option key={r} value={r}>
          {roleLabel(r)}
        </option>
      ))}
    </select>
  );
}

function CreateEmployeeModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/employees", { body: form });
      if (error) throw new Error(errorText(error, "не удалось создать"));
    },
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setFormError(null);
      onDone();
    },
    onError: (error: Error) => setFormError(error.message),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    createMutation.mutate();
  }

  return (
    <Modal
      title="Новый сотрудник"
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" form="create-employee" className="btn btn-primary" disabled={createMutation.isPending}>
            Создать
          </button>
        </>
      }
    >
      <form id="create-employee" onSubmit={handleSubmit}>
        <label className="field">
          ФИО
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
        </label>
        <label className="field">
          Email
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        </label>
        <label className="field">
          Роль
          <RoleSelect value={form.role} onChange={(role) => setForm({ ...form, role })} />
        </label>
        <label className="field">
          Пароль
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
          <span className="field-hint">Не короче 12 символов. Сотрудник сможет войти сразу.</span>
        </label>
        {formError && <ErrorState message={formError} />}
      </form>
    </Modal>
  );
}

function EditEmployeeModal({
  employee,
  onClose,
  onDone,
}: {
  employee: EmployeeRow;
  onClose: () => void;
  onDone: () => void;
}) {
  const [role, setRole] = useState(employee.role);
  const [password, setPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const updateMutation = useMutation({
    mutationFn: async (patch: { active?: boolean; role?: string; password?: string }) => {
      const { error } = await api.PATCH("/api/employees/{employee_id}", {
        params: { path: { employee_id: employee.id } },
        body: patch,
      });
      if (error) throw new Error(errorText(error, "не удалось изменить"));
    },
    onSuccess: onDone,
    onError: (error: Error) => setFormError(error.message),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    const patch: { role?: string; password?: string } = {};
    if (role !== employee.role) patch.role = role;
    if (password) patch.password = password;
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    updateMutation.mutate(patch);
  }

  return (
    <Modal
      title={employee.full_name}
      open
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className="btn"
            disabled={updateMutation.isPending}
            onClick={() => updateMutation.mutate({ active: !employee.active })}
          >
            {employee.active ? "Отключить доступ" : "Включить доступ"}
          </button>
          <span className="toolbar__spacer" />
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" form="edit-employee" className="btn btn-primary" disabled={updateMutation.isPending}>
            Сохранить
          </button>
        </>
      }
    >
      <form id="edit-employee" onSubmit={handleSubmit}>
        <p className="muted">{employee.email}</p>
        <label className="field">
          Роль
          <RoleSelect value={role} onChange={setRole} />
        </label>
        <label className="field">
          Новый пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="field-hint">Оставьте пустым, чтобы не менять.</span>
        </label>
        {formError && <ErrorState message={formError} />}
      </form>
    </Modal>
  );
}
