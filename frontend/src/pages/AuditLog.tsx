import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;

export function AuditLogPage() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["audit-log", action, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/audit-log", {
        params: { query: { action: action || undefined, page, page_size: PAGE_SIZE } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <h1>Журнал аудита</h1>

      <div className="toolbar">
        <label>
          Действие
          <select
            value={action}
            onChange={(e) => {
              setPage(1);
              setAction(e.target.value);
            }}
          >
            <option value="">любое</option>
            <option value="view_application">view_application</option>
            <option value="view_document">view_document</option>
            <option value="view_credit_report">view_credit_report</option>
            <option value="login">login</option>
            <option value="login_failed">login_failed</option>
            <option value="logout">logout</option>
            <option value="lift_freeze">lift_freeze</option>
            <option value="mark_false_positive">mark_false_positive</option>
            <option value="manage_employee">manage_employee</option>
            <option value="manage_reference">manage_reference</option>
            <option value="retry_check">retry_check</option>
          </select>
        </label>
      </div>

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить журнал аудита.</p>}
      {query.data && query.data.items.length === 0 && <p className="muted">Записей нет.</p>}
      {query.data && query.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Действие</th>
              <th>Объект</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((row) => (
              <tr key={row.id}>
                <td className="muted">{formatDateTime(row.created_at)}</td>
                <td>{row.action}</td>
                <td className="muted">
                  {row.object_type ?? "—"} {row.object_id ?? ""}
                </td>
                <td className="muted">{row.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {query.data && query.data.total > 0 && (
        <div className="pagination">
          <button className="btn btn-small" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            ← назад
          </button>
          <span>
            Страница {query.data.page} из {Math.max(1, Math.ceil(query.data.total / query.data.page_size))}
          </span>
          <button
            className="btn btn-small"
            disabled={page * PAGE_SIZE >= query.data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            вперёд →
          </button>
        </div>
      )}
    </section>
  );
}
