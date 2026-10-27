import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination, Segmented } from "../components/ui";
import { contractStatusLabel, contractStatusTone } from "../lib/labels";
import { formatAmount, formatDate } from "../lib/format";

const PAGE_SIZE = 25;
// «overdue» — не статус договора, а действующие с просрочкой (фильтр API).
const FILTERS = ["", "active", "overdue", "closed", "defaulted", "repossessed"] as const;
type Filter = (typeof FILTERS)[number];

function filterLabel(f: Filter): string {
  if (f === "") return "Все";
  if (f === "overdue") return "В просрочке";
  return contractStatusLabel(f);
}

export function ContractsListPage() {
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<Filter>("");

  const summary = useQuery({
    queryKey: ["contracts-summary"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/contracts/summary");
      if (error) throw error;
      return data;
    },
  });

  const query = useQuery({
    queryKey: ["contracts", filter, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/contracts", {
        params: {
          query: {
            page,
            page_size: PAGE_SIZE,
            status_filter: filter && filter !== "overdue" ? filter : undefined,
            overdue: filter === "overdue" || undefined,
          },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  const counts = summary.data;
  const countOf = (f: Filter): number | undefined => {
    if (!counts) return undefined;
    if (f === "") return Object.values(counts.by_status).reduce((a, b) => a + b, 0);
    if (f === "overdue") return counts.overdue;
    return counts.by_status[f] ?? 0;
  };

  return (
    <section>
      <PageHeader
        title="Договоры"
        description="Выданные рассрочки из выгрузки учётной системы: сколько оплачено и есть ли просрочка. Только просмотр."
      />
      <Segmented
        label="Состояние договора"
        value={filter}
        onChange={(next) => {
          setPage(1);
          setFilter(next);
        }}
        options={FILTERS.map((f) => ({ value: f, label: filterLabel(f), count: countOf(f) }))}
      />

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить список договоров." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <EmptyState
          title={filter ? "Таких договоров нет" : "Договоров пока нет"}
          hint={filter ? undefined : "Появятся после загрузки выгрузки договоров в Настройках → Импорт."}
        />
      )}

      {query.data && query.data.items.length > 0 && (
        <table className="table table--list contracts-table">
          <thead>
            <tr>
              <th>Договор</th>
              <th className="num">К оплате</th>
              <th>Оплачено</th>
              <th>Просрочка</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => {
              const total = Number(item.total_amount);
              const paid = Number(item.paid_amount);
              const share = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;
              const late = (item.days_past_due ?? 0) > 0;
              return (
                <tr key={item.id}>
                  <td>
                    <Link className="contracts-table__number" to={`/contracts/${item.id}`}>
                      {item.external_number ?? item.id}
                    </Link>
                    <div className="contracts-table__sub">выдан {formatDate(item.issued_at)}</div>
                  </td>
                  <td className="num">
                    {formatAmount(item.total_amount)} ₽
                    <div className="contracts-table__sub">на {item.term_months} мес.</div>
                  </td>
                  <td>
                    <div className="paid">
                      <div className="paid__bar" aria-hidden="true">
                        <i style={{ width: `${share}%` }} />
                      </div>
                      <span className="paid__text">
                        {formatAmount(item.paid_amount)} ₽ <span className="muted">· {share}%</span>
                      </span>
                    </div>
                  </td>
                  <td>
                    {item.days_past_due == null ? (
                      <span className="muted">—</span>
                    ) : late ? (
                      <span className="late">
                        {item.days_past_due} дн. · {formatAmount(item.overdue_amount ?? "0")} ₽
                      </span>
                    ) : (
                      <span className="muted">нет</span>
                    )}
                  </td>
                  <td>
                    <Badge value={item.status} label={contractStatusLabel(item.status)} tone={contractStatusTone(item.status)} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {query.data && <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />}
    </section>
  );
}
