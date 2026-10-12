import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination, Toolbar } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { AUDIT_ACTION_LABELS, auditActionLabel, auditObjectLabel } from "../lib/labels";

const PAGE_SIZE = 50;

// T23: фильтры по сотруднику и действию (оба есть в API), подписи вместо
// кодов действий, колонка «Кто» — имя сотрудника, а не только его id.
export function AuditLogPage() {
  const [action, setAction] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [page, setPage] = useState(1);

  const employeesQuery = useQuery({
    queryKey: ["employees"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/employees", { params: { query: { page: 1, page_size: 100 } } });
      if (error) throw error;
      return data;
    },
  });
  const employeeNames = new Map((employeesQuery.data?.items ?? []).map((e) => [e.id, e.full_name]));

  const query = useQuery({
    queryKey: ["audit-log", action, employeeId, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/audit-log", {
        params: {
          query: { action: action || undefined, employee_id: employeeId || undefined, page, page_size: PAGE_SIZE },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <PageHeader
        title="Журнал аудита"
        description="Кто и когда входил, открывал карточки, фото и кредитные отчёты, менял сотрудников, справочники и правила. Записи не удаляются и не редактируются."
      />

      <Toolbar>
        <select
          className="select"
          aria-label="Сотрудник"
          value={employeeId}
          onChange={(e) => {
            setPage(1);
            setEmployeeId(e.target.value);
          }}
        >
          <option value="">Все сотрудники</option>
          {(employeesQuery.data?.items ?? []).map((e) => (
            <option key={e.id} value={e.id}>
              {e.full_name}
            </option>
          ))}
        </select>
        <select
          className="select"
          aria-label="Действие"
          value={action}
          onChange={(e) => {
            setPage(1);
            setAction(e.target.value);
          }}
        >
          <option value="">Все действия</option>
          {Object.entries(AUDIT_ACTION_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {query.data && <span className="toolbar__spacer toolbar__count">Записей: {query.data.total}</span>}
      </Toolbar>

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить журнал аудита." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <EmptyState title="Записей нет" hint="Измените фильтр по сотруднику или действию." />
      )}
      {query.data && query.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Кто</th>
              <th>Действие</th>
              <th>Объект</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((row) => (
              <tr key={row.id}>
                <td className="muted">{formatDateTime(row.created_at)}</td>
                <td>{row.employee_id ? employeeNames.get(row.employee_id) ?? "—" : "—"}</td>
                <td>{auditActionLabel(row.action)}</td>
                <td className="muted">
                  {row.object_type === "application" && row.object_id ? (
                    <Link to={`/applications/${row.object_id}`}>заявка</Link>
                  ) : (
                    auditObjectLabel(row.object_type)
                  )}
                </td>
                <td className="muted">{row.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {query.data && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
      )}
    </section>
  );
}
