import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination, Segmented } from "../components/ui";
import { contractStatusLabel } from "../lib/labels";
import { formatAmount, formatDate } from "../lib/format";

const PAGE_SIZE = 25;
const STATUSES = ["", "active", "closed", "defaulted", "repossessed"] as const;
type StatusFilter = (typeof STATUSES)[number];

export function ContractsListPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<StatusFilter>("");

  const query = useQuery({
    queryKey: ["contracts", status, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/contracts", {
        params: { query: { page, page_size: PAGE_SIZE, status_filter: status || undefined } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <PageHeader
        title="Договоры"
        description="Договоры рассрочки из учётной системы заказчика (раздел «Импорт»). Просрочка пересчитывается ежедневно по графику и платежам."
      />
      <Segmented
        label="Состояние договора"
        value={status}
        onChange={(next) => {
          setPage(1);
          setStatus(next);
        }}
        options={STATUSES.map((s) => ({ value: s, label: s ? contractStatusLabel(s) : "Все" }))}
      />

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить список договоров." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <EmptyState
          title={status ? "Договоров в этом состоянии нет" : "Договоров пока нет"}
          hint={status ? undefined : "Появятся после загрузки выгрузки договоров в Настройках → Импорт."}
        />
      )}

      {query.data && query.data.items.length > 0 && (
        <table className="table table--list">
          <thead>
            <tr>
              <th>№ у заказчика</th>
              <th>Выдан</th>
              <th className="num">Сумма</th>
              <th className="num">Срок</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/contracts/${item.id}`}>{item.external_number ?? item.id}</Link>
                </td>
                <td className="muted">{formatDate(item.issued_at)}</td>
                <td className="num">{formatAmount(item.total_amount)} ₽</td>
                <td className="num">{item.term_months} мес.</td>
                <td>
                  <Badge value={item.status} label={contractStatusLabel(item.status)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {query.data && <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />}
    </section>
  );
}
