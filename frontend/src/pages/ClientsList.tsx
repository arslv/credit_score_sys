import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination } from "../components/ui";

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
      <PageHeader
        title="Клиенты"
        description="Один клиент — один человек по паспорту: все его заявки собраны вместе, повторный клиент виден сразу."
      />

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить список клиентов." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <EmptyState title="Клиентов пока нет" hint="Клиент появляется после распознавания паспорта по первой заявке." />
      )}

      {query.data && query.data.items.length > 0 && (
        <table className="table table--list">
          <thead>
            <tr>
              <th>ФИО</th>
              <th className="num">Заявок</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/clients/${item.id}`}>{item.name}</Link>
                </td>
                <td className="num">{item.applications_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {query.data && <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />}
    </section>
  );
}
