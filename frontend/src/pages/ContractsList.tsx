import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { formatAmount, formatDate } from "../lib/format";

const PAGE_SIZE = 25;

export function ContractsListPage() {
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["contracts", page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/contracts", {
        params: { query: { page, page_size: PAGE_SIZE } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <h1>Договоры</h1>

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить список договоров.</p>}
      {query.data && query.data.items.length === 0 && (
        <p className="muted">Договоров нет — появятся после импорта платежей (T15).</p>
      )}

      {query.data && query.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>№ у заказчика</th>
              <th>Сумма</th>
              <th>Срок</th>
              <th>Состояние</th>
              <th>Выдан</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/contracts/${item.id}`}>{item.external_number ?? item.id}</Link>
                </td>
                <td>{formatAmount(item.total_amount)}</td>
                <td>{item.term_months} мес.</td>
                <td>
                  <Badge value={item.status} />
                </td>
                <td className="muted">{formatDate(item.issued_at)}</td>
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
