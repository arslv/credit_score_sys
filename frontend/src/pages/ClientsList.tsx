import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ErrorState, LoadingState, Pagination, SearchInput, Toolbar, useDebounced } from "../components/ui";
import { initialsOf } from "../lib/format";

const PAGE_SIZE = 24;

function applicationsWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "заявка";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "заявки";
  return "заявок";
}

// Портфель → Клиенты (вид от 16.11.2026): карточки клиентов сеткой — аватар,
// ФИО, сколько заявок; повторный клиент (2+ заявки) отмечен.
export function ClientsListPage() {
  const [page, setPage] = useState(1);
  // Поиск (решение владельца 16.11.2026): ФИО, телефон, ИНН, номер паспорта.
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());

  const query = useQuery({
    queryKey: ["clients", page, q],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/clients", {
        params: { query: { page, page_size: PAGE_SIZE, q: q || undefined } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section className="pg">
      <header className="pg-head">
        <div>
          <h1>Клиенты</h1>
          <p className="pg-head__sub">Один клиент — один человек по паспорту: все его заявки собраны вместе.</p>
        </div>
        {query.data && (
          <div className="pg-stats">
            <div className="pg-stat">
              <span className="pg-stat__value">{query.data.total}</span>
              <span className="pg-stat__label">клиентов</span>
            </div>
          </div>
        )}
      </header>

      <Toolbar>
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
          placeholder="ФИО, телефон, ИНН или паспорт"
          label="Поиск клиентов"
        />
      </Toolbar>

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить список клиентов." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <div className="pg-empty">
          <div className="pg-empty__title">{q ? "Никого не нашли" : "Клиентов пока нет"}</div>
          <div className="pg-empty__hint">
            {q ? "Проверьте написание или ищите по телефону." : "Клиент появляется после проверки паспорта по первой заявке."}
          </div>
        </div>
      )}

      {query.data && query.data.items.length > 0 && (
        <ul className="cl-grid">
          {query.data.items.map((item) => (
            <li key={item.id}>
              <Link className="cl-card" to={`/clients/${item.id}`}>
                <span className="pg-avatar" aria-hidden="true">
                  {initialsOf(item.name)}
                </span>
                <span className="cl-card__text">
                  <span className="cl-card__name" title={item.name}>{item.name}</span>
                  <span className="cl-card__meta">
                    {item.applications_count} {applicationsWord(item.applications_count)}
                    {item.applications_count > 1 && <span className="cl-card__tag">повторный</span>}
                  </span>
                </span>
                <span className="cl-card__chevron" aria-hidden="true">
                  ›
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {query.data && <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />}
    </section>
  );
}
