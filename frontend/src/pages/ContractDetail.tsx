import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { formatAmount, formatDate } from "../lib/format";

export function ContractDetailPage() {
  const { id } = useParams<{ id: string }>();

  const query = useQuery({
    queryKey: ["contract", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/contracts/{contract_id}", {
        params: { path: { contract_id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
  });

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) return <p role="alert" className="alert">Не удалось загрузить договор.</p>;

  const contract = query.data;

  return (
    <section>
      <p>
        <Link to="/contracts">← к списку договоров</Link>
      </p>
      <header className="card">
        <h1>Договор {contract.external_number ?? contract.id}</h1>
        <p>
          <Badge value={contract.status} /> · {formatAmount(contract.total_amount)} ₽ на {contract.term_months} мес.
          · выдан {formatDate(contract.issued_at)}
          {contract.closed_at && ` · закрыт ${formatDate(contract.closed_at)}`}
          {contract.closure_reason && ` (${contract.closure_reason})`}
        </p>
        {contract.closed_at && contract.closure_outcome === "unknown" && (
          <p role="alert" className="alert">
            Причина закрытия не распознана конфигом — таргет для этого договора не определён (NULL), не считается
            ни хорошим, ни плохим исходом.
          </p>
        )}
        <p className="muted">
          Основной долг {formatAmount(contract.principal)} ₽ · наценка {formatAmount(contract.markup)} ₽ ·
          первый взнос {formatAmount(contract.down_payment)} ₽ ·{" "}
          {contract.application_id ? (
            <Link to={`/applications/${contract.application_id}`}>заявка</Link>
          ) : (
            "заявка не привязана"
          )}
        </p>
        {contract.current_delinquency && (
          <p>
            <span
              className={`badge ${contract.current_delinquency.days_past_due > 0 ? "badge-danger" : "badge-success"}`}
            >
              {contract.current_delinquency.days_past_due > 0
                ? `просрочка ${contract.current_delinquency.days_past_due} дн., ${formatAmount(contract.current_delinquency.overdue_amount)} ₽`
                : "без просрочки"}
            </span>{" "}
            <span className="muted">на {formatDate(contract.current_delinquency.as_of)}</span>
          </p>
        )}
      </header>

      <h2>
        График платежей{" "}
        {contract.schedule_source === "imported" ? (
          <span className="badge badge-success" title="Загружен отдельным файлом, заменяет сгенерированный">
            из выгрузки
          </span>
        ) : (
          <span className="badge badge-neutral" title="Файла графика не было — равные платежи, запасной вариант">
            сгенерирован
          </span>
        )}
      </h2>
      {contract.schedule.length === 0 && <p className="muted">Графика нет.</p>}
      {contract.schedule.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>№ периода</th>
              <th>Срок</th>
              <th>Сумма</th>
            </tr>
          </thead>
          <tbody>
            {contract.schedule.map((s) => (
              <tr key={s.period_no}>
                <td>{s.period_no}</td>
                <td className="muted">{formatDate(s.due_date)}</td>
                <td>{formatAmount(s.due_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Платежи</h2>
      {contract.payments.length === 0 && <p className="muted">Платежей нет.</p>}
      {contract.payments.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Сумма</th>
              <th>Тип</th>
              <th>Источник</th>
            </tr>
          </thead>
          <tbody>
            {contract.payments.map((p) => (
              <tr key={p.id}>
                <td className="muted">{formatDate(p.paid_at)}</td>
                <td>{formatAmount(p.amount)}</td>
                <td>
                  {p.kind === "penalty" && <span className="badge badge-neutral">неустойка</span>}
                  {p.kind === "reversal" && <span className="badge badge-danger">сторно</span>}
                  {p.kind === "regular" && <span className="muted">по графику</span>}
                </td>
                <td>{p.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
