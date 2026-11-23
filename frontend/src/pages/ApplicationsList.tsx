import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { EmptyState, ErrorState, LoadingState, PageHeader, Pagination, SearchInput, useDebounced } from "../components/ui";
import { formatAmount, formatDateTime, formatElapsed, formatShortDateTime } from "../lib/format";
import { applicationStatusLabel } from "../lib/labels";

const PAGE_SIZE = 25;

// Быстрые фильтры и их счётчики — с сервера. Решение владельца 23.11.2026:
// три вкладки — «Новые» (итога ещё нет), «Серая зона», «Все».
type View = "new" | "gray" | "all";

const VIEW_LABELS: Record<View, string> = {
  new: "Новые",
  gray: "Серая зона",
  all: "Все",
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

// Заявку без CRM заводят admin и operator (app/api/manual_applications.py).
const CREATE_ROLES = ["admin", "operator"];

export function ApplicationsListPage() {
  const { employee } = useAuth();
  const navigate = useNavigate();
  const [view, setView] = useState<View>("new");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  // Поиск (решение владельца 16.11.2026): ФИО, телефон, № заявки, № в CRM, паспорт.
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());

  const countsQuery = useQuery({
    queryKey: ["applications", "quick-counts"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications/quick-counts");
      if (error) throw error;
      return data;
    },
  });

  const query = useQuery({
    queryKey: ["applications", view, filters, page, q],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications", {
        params: {
          query: {
            view,
            q: q || undefined,
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
  // T23: одна строка на заявку (~36px) — номер у заказчика и товар отдельными
  // колонками, наш порядковый номер — в подсказке; раньше вторая строка под ФИО.
  // Колонки — как в списке CRM (решение владельца 23.11.2026). Что сделать,
  // сколько ждёт и «оператор изменил статус» — второй строкой под статусом.
  const columns = [
    { key: "number", title: "id" },
    { key: "point", title: "Точка продаж" },
    { key: "client", title: "Клиент" },
    { key: "goods", title: "Продукт" },
    { key: "term", title: "Срок", num: true },
    { key: "amount", title: "Сумма", num: true },
    { key: "ankets", title: "Анкет", num: true },
    { key: "decided", title: "Время принятия" },
    { key: "status", title: "Статус" },
    { key: "menu", title: "Действия" },
  ];
  const views = Object.keys(VIEW_LABELS) as View[];

  // Строка целиком — ссылка на заявку; Ctrl/⌘ — в новой вкладке. Клики по
  // ссылке и меню «⋮» строка не перехватывает.
  function openRow(e: React.MouseEvent, id: string) {
    if ((e.target as HTMLElement).closest("a, button, [role='menu']")) return;
    if (window.getSelection()?.toString()) return; // выделяли текст — не открываем
    const url = `/applications/${id}`;
    if (e.ctrlKey || e.metaKey) window.open(url, "_blank");
    else navigate(url);
  }

  return (
    <section>
      <PageHeader
        title="Заявки"
        actions={
          CREATE_ROLES.includes(employee?.role ?? "") ? (
            <Link className="btn btn-primary" to="/applications/new">
              Новая заявка
            </Link>
          ) : undefined
        }
        description="«Новые» — заявки, по которым ещё нет итога: идут проверки или нужно действие."
      />

      <div className="list-search">
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
            // Ищем по всем заявкам, а не только в выбранной вкладке.
            if (value.trim() && view !== "all") setView("all");
          }}
          placeholder="ФИО, телефон, № заявки или паспорт"
          label="Поиск заявок"
        />
      </div>

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
            {counts && counts[key] != null && (
              <span className="chip-count">
                <span className="sr-only"> · </span>
                {counts[key]}
              </span>
            )}
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
              <option value="rejected_a,rejected_b">Отказ</option>
              <option value="gray_zone">{applicationStatusLabel("gray_zone")}</option>
              <option value="approved">{applicationStatusLabel("approved")}</option>
              <option value="ocr_failed">{applicationStatusLabel("ocr_failed")}</option>
              <option value="awaiting_data">{applicationStatusLabel("awaiting_data")}</option>
              <option value="awaiting_bki">{applicationStatusLabel("awaiting_bki")}</option>
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
          title={view === "new" ? "Новых заявок нет — всё разобрано" : "Заявок не нашлось"}
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
        <div className="crm-card">
        <table className="table table--list table--crm">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} className={column.num ? "num" : undefined}>
                  {column.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id} className={`crm-row crm-row--${statusTone(item.status)}`} onClick={(e) => openRow(e, item.id)}>
                <td className="cell-id" title={`Заявка №${item.number}`}>
                  {item.source === "manual" ? (
                    <>
                      {item.number}
                      <span className="crm-tag">вручную</span>
                    </>
                  ) : (
                    item.external_id
                  )}
                </td>
                <td className="cell-ellipsis muted" title={item.sales_point ?? undefined}>
                  {item.sales_point ?? "—"}
                </td>
                <td className="cell-ellipsis cell-client">
                  <Link to={`/applications/${item.id}`}>{item.client_name ?? "—"}</Link>
                </td>
                <td className="cell-ellipsis" title={item.goods_name}>
                  {item.goods_name && item.goods_name !== "—" ? item.goods_name : "—"}
                </td>
                {/* До «Рассмотреть» сделки ещё нет (нули-заглушки) — прочерк, не «0 ₽». */}
                <td className="num">{item.term_months > 0 ? item.term_months : "—"}</td>
                <td className="num cell-amount">{Number(item.goods_price) > 0 ? `${formatAmount(item.goods_price)} ₽` : "—"}</td>
                <td className="num">{item.anket_count}</td>
                <td className="muted" title={item.decided_at ? formatDateTime(item.decided_at) : undefined}>
                  {item.decided_at ? formatShortDateTime(item.decided_at) : "—"}
                </td>
                <td className="cell-status">
                  <span className={`status-pill status-pill--${statusTone(item.status)}`}>
                    {applicationStatusLabel(item.status)}
                  </span>
                  {item.action_text && (
                    <span className="cell-status__sub">
                      <span className={`action-dot action-dot--${item.action_kind}`} aria-hidden="true" />
                      {item.action_text}
                      {item.waiting_since && <span className="muted"> · {formatElapsed(item.waiting_since)}</span>}
                    </span>
                  )}
                  {item.has_override && item.override_matches_system === false && (
                    <span className="cell-status__sub" title="Итог оператора не совпадает с последним решением системы">
                      Оператор изменил статус
                    </span>
                  )}
                </td>
                <td className="cell-menu">
                  <RowMenu id={item.id} canEdit={CREATE_ROLES.includes(employee?.role ?? "")} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {query.data && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
      )}
    </section>
  );
}

// «⋮» у строки — действия с заявкой, как в списке CRM. Меню «плавает»
// поверх страницы (position: fixed): таблица в рамке с прокруткой и иначе
// его обрезает. У нижнего края экрана открывается вверх.
function RowMenu({ id, canEdit }: { id: string; canEdit: boolean }) {
  const [place, setPlace] = useState<React.CSSProperties | null>(null);
  const open = place !== null;
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPlace(null);
    };
    const hide = () => setPlace(null);
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [open]);

  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    if (open) return setPlace(null);
    const r = e.currentTarget.getBoundingClientRect();
    const right = window.innerWidth - r.right;
    setPlace(
      window.innerHeight - r.bottom < 120
        ? { position: "fixed", right, bottom: window.innerHeight - r.top + 4 }
        : { position: "fixed", right, top: r.bottom + 4 },
    );
  }
  return (
    <div className="row-menu" ref={ref}>
      <button
        type="button"
        className="row-menu__btn"
        aria-label="Действия с заявкой"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        ⋮
      </button>
      {open && (
        <div className="row-menu__list" role="menu" style={place ?? undefined}>
          <button type="button" role="menuitem" onClick={() => navigate(`/applications/${id}`)}>
            Открыть заявку
          </button>
          {canEdit && (
            <button type="button" role="menuitem" onClick={() => navigate(`/applications/${id}/edit`)}>
              Изменить анкету
            </button>
          )}
        </div>
      )}
    </div>
  );
}
