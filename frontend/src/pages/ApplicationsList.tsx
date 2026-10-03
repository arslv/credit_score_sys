import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { formatAmount, formatDateTime } from "../lib/format";
import { applicationStatusLabel } from "../lib/labels";

const PAGE_SIZE = 25;

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
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["applications", filters, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications", {
        params: {
          query: {
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

  return (
    <section>
      <h1>Заявки</h1>

      <div className="toolbar">
        <label>
          Состояние
          <select value={filters.status} onChange={(e) => updateFilters({ status: e.target.value })}>
            <option value="">любое</option>
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
          только ocr_failed
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

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить список заявок.</p>}
      {query.data && query.data.items.length === 0 && <p className="muted">Ничего не найдено.</p>}

      {query.data && query.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>№</th>
              <th>№ заказчика</th>
              <th>Дата</th>
              <th>ФИО</th>
              <th>Сумма</th>
              <th>Товар</th>
              <th>Состояние</th>
              <th>Стадия А</th>
              <th>Стадия Б</th>
              <th>Скор</th>
              <th>Итог оператора</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/applications/${item.id}`}>{item.number}</Link>
                </td>
                <td>{item.external_id}</td>
                <td>{formatDateTime(item.submitted_at)}</td>
                <td>{item.client_name ?? "—"}</td>
                <td>{formatAmount(item.goods_price)}</td>
                <td>{item.goods_name}</td>
                <td>
                  <Badge value={item.status} label={applicationStatusLabel(item.status)} />
                </td>
                <td>
                  <Badge value={item.stage_a_outcome} />
                </td>
                <td>
                  <Badge value={item.stage_b_outcome} />
                </td>
                <td>{item.score ?? "—"}</td>
                <td>
                  {item.has_override ? (
                    item.override_matches_system ? (
                      <span className="badge badge-neutral">совпал</span>
                    ) : (
                      <span className="badge badge-warning">разошёлся</span>
                    )
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
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
