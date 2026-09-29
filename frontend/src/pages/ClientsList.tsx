import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

const PAGE_SIZE = 25;

export function ClientsListPage() {
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["clients", page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/clients", {
        params: { query: { page, page_size: PAGE_SIZE } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <h1>Клиенты</h1>

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить список клиентов.</p>}
      {query.data && query.data.items.length === 0 && <p className="muted">Клиентов нет.</p>}

      {query.data && query.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>ФИО</th>
              <th>Заявок</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/clients/${item.id}`}>{item.name}</Link>
                </td>
                <td>{item.applications_count}</td>
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
