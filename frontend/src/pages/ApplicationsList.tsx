import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination } from "../components/ui";
import { formatAmount, formatElapsed } from "../lib/format";
import { applicationStatusLabel } from "../lib/labels";

const PAGE_SIZE = 25;

// T17, часть 4 (макет «Заявки»). Быстрые фильтры и их счётчики — с сервера.
// «Требуют действия» — очередь РОЛИ (оператор / комплаенс / риск-менеджер);
// у наблюдателя и администратора очереди нет, чипа нет.
type View = "action" | "gray" | "today" | "all";

const VIEW_LABELS: Record<View, string> = {
  action: "Требуют действия",
  gray: "Серая зона",
  today: "Сегодня",
  all: "Все",
};

const QUEUE_ROLES = ["operator", "compliance", "risk_manager"];

// Колонки, которые у роли всегда пусты, не показываем: у комплаенса очередь —
// заморозки по перечню, без балла БКИ и без итога оператора.
// T23: у ролей без очереди (наблюдатель, администратор) «Нужно сделать» и
// «Ждёт» всегда пусты — не занимаем ими ширину.
const HIDDEN_COLUMNS_BY_ROLE: Record<string, Set<string>> = {
  compliance: new Set(["score", "override"]),
  viewer: new Set(["action", "waiting"]),
  admin: new Set(["action", "waiting"]),
};

// Тон пилюли статуса: одобрено — зелёный, отказ — красный, серая зона — янтарный,
// всё «в процессе» — нейтральный серо-синий. Цвета — токены в index.css.
function statusTone(status: string): "progress" | "approve" | "gray" | "reject" {
  if (status === "approved" || status === "contracted") return "approve";
  if (status === "rejected_a" || status === "rejected_b" || status === "expired") return "reject";
  if (status === "gray_zone") return "gray";
  return "progress";
}

interface Filters {
  status: string;
  discrepanciesOnly: boolean;
  ocrFailedOnly: boolean;
  sourceUnavailableOnly: boolean;
}

const EMPTY_FILTERS: Filters = {
  status: "",
  discrepanciesOnly: false,
  ocrFailedOnly: false,
  sourceUnavailableOnly: false,
};

export function ApplicationsListPage() {
  const { employee } = useAuth();
  const hasQueue = QUEUE_ROLES.includes(employee?.role ?? "");
  const [view, setView] = useState<View>(hasQueue ? "action" : "all");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);

  const countsQuery = useQuery({
    queryKey: ["applications", "quick-counts"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications/quick-counts");
      if (error) throw error;
      return data;
    },
  });

  const query = useQuery({
    queryKey: ["applications", view, filters, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications", {
        params: {
          query: {
            view,
            status: filters.status || undefined,
            discrepancies_only: filters.discrepanciesOnly || undefined,
            ocr_failed_only: filters.ocrFailedOnly,
            source_unavailable_only: filters.sourceUnavailableOnly,
            page,
            page_size: PAGE_SIZE,
          },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  function updateFilters(patch: Partial<Filters>) {
    setPage(1);
    setFilters((prev) => ({ ...prev, ...patch }));
  }

  function selectView(next: View) {
    setPage(1);
    setView(next);
  }

  const counts = countsQuery.data;
  const hidden = HIDDEN_COLUMNS_BY_ROLE[employee?.role ?? ""] ?? new Set<string>();
  // T23: одна строка на заявку (~36px) — номер у заказчика и товар отдельными
  // колонками, наш порядковый номер — в подсказке; раньше вторая строка под ФИО.
  const columns = [
    { key: "number", title: "№ у заказчика" },
    { key: "client", title: "Клиент" },
    { key: "goods", title: "Товар" },
    { key: "amount", title: "Сумма / срок" },
    { key: "status", title: "Статус" },
    { key: "score", title: "Балл БКИ" },
    { key: "action", title: "Нужно сделать" },
    { key: "waiting", title: "Ждёт" },
    { key: "override", title: "Итог оператора" },
  ].filter((column) => !hidden.has(column.key));
  const views = (Object.keys(VIEW_LABELS) as View[]).filter((key) => key !== "action" || counts?.action != null);

  return (
    <section>
      <PageHeader
        title="Заявки"
        description={
          hasQueue
            ? "«Требуют действия» — ваша очередь: то, что ждёт именно вашей роли. Остальные вкладки — вся картина."
            : "Все заявки, пришедшие от заказчика, и решения по ним."
        }
      />

      <div className="chips" role="group" aria-label="Быстрые фильтры">
        {views.map((key) => (
          <button
            key={key}
            type="button"
            className={`chip${view === key ? " chip--active" : ""}`}
            aria-pressed={view === key}
            onClick={() => selectView(key)}
          >
            {VIEW_LABELS[key]}
            {counts && counts[key] != null && <span className="chip-count"> · {counts[key]}</span>}
          </button>
        ))}
      </div>

      <details className="more-filters">
        <summary>Ещё фильтры</summary>
        <div className="toolbar">
          <label>
            Статус
            <select value={filters.status} onChange={(e) => updateFilters({ status: e.target.value })}>
              <option value="">любой</option>
              <option value="to_operator">{applicationStatusLabel("to_operator")}</option>
              <option value="rejected_a">{applicationStatusLabel("rejected_a")}</option>
              <option value="rejected_b">{applicationStatusLabel("rejected_b")}</option>
              <option value="gray_zone">{applicationStatusLabel("gray_zone")}</option>
              <option value="approved">{applicationStatusLabel("approved")}</option>
              <option value="ocr_failed">{applicationStatusLabel("ocr_failed")}</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={filters.discrepanciesOnly}
              onChange={(e) => updateFilters({ discrepanciesOnly: e.target.checked })}
            />
            только расхождения с оператором
          </label>
          <label>
            <input
              type="checkbox"
              checked={filters.ocrFailedOnly}
              onChange={(e) => updateFilters({ ocrFailedOnly: e.target.checked })}
            />
            только не распознанные паспорта
          </label>
          <label>
            <input
              type="checkbox"
              checked={filters.sourceUnavailableOnly}
              onChange={(e) => updateFilters({ sourceUnavailableOnly: e.target.checked })}
            />
            есть недоступный источник
          </label>
        </div>
      </details>

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить список заявок." onRetry={() => void query.refetch()} />}
      {query.data && query.data.items.length === 0 && (
        <EmptyState
          title={view === "action" ? "Очередь пуста — всё разобрано" : "Заявок не нашлось"}
          action={
            view !== "all" || filters !== EMPTY_FILTERS ? (
              <button
                className="btn"
                onClick={() => {
                  setFilters(EMPTY_FILTERS);
                  selectView("all");
                }}
              >
                Показать все заявки
              </button>
            ) : undefined
          }
        />
      )}

      {query.data && query.data.items.length > 0 && (
        <table className="table table--list">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.title}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td className="muted cell-ellipsis cell-id" title={`Заявка №${item.number}`}>
                  {item.external_id}
                </td>
                <td className="cell-ellipsis">
                  <Link to={`/applications/${item.id}`}>{item.client_name ?? "—"}</Link>
                </td>
                <td className="cell-ellipsis muted" title={item.goods_name}>
                  {item.goods_name}
                </td>
                <td className="num">
                  {/* До «Рассмотреть» сделки ещё нет (T18: нули-заглушки) — прочерк, не «0 ₽». */}
                  {item.term_months > 0 ? `${formatAmount(item.requested_amount)} ₽ / ${item.term_months} мес.` : "—"}
                </td>
                <td>
                  <span className={`status-pill status-pill--${statusTone(item.status)}`}>
                    {applicationStatusLabel(item.status)}
                  </span>
                </td>
                {!hidden.has("score") && (
                  <td className="num">{item.score != null ? Math.round(Number(item.score)) : ""}</td>
                )}
                {!hidden.has("action") && (
                  <td>
                    {item.action_text && (
                      <>
                        <span className={`action-dot action-dot--${item.action_kind}`} aria-hidden="true" />
                        {item.action_text}
                      </>
                    )}
                  </td>
                )}
                {!hidden.has("waiting") && (
                  <td className="muted num">
                    {/* «Ждёт» — только у заявок очереди роли: момент встал в очередь. */}
                    {item.action_text && item.waiting_since ? formatElapsed(item.waiting_since) : ""}
                  </td>
                )}
                {!hidden.has("override") && (
                  <td className="cell-wrap">
                    {item.has_override && item.override_matches_system === false ? (
                      <span title="Итог оператора не совпадает с последним решением системы">
                        Оператор изменил статус
                      </span>
                    ) : null}
                  </td>
                )}
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
