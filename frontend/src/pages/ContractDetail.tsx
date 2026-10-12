import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";
import { contractStatusLabel } from "../lib/labels";
import { formatAmount, formatDate } from "../lib/format";

const PAYMENT_SOURCE_LABELS: Record<string, string> = { import: "выгрузка", manual: "вручную" };
const PAYMENT_KIND_LABELS: Record<string, string> = {
  regular: "по графику",
  penalty: "неустойка",
  reversal: "сторно",
};

// T23: условия и состояние — двумя колонками «подпись — значение», график и
// платежи — рядом (короткие таблицы), вместо одного длинного столбца.
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

  if (query.isLoading) return <LoadingState />;
  if (query.isError || !query.data) return <ErrorState message="Не удалось загрузить договор." />;

  const contract = query.data;
  const delinquency = contract.current_delinquency;
  const paid = contract.payments
    .filter((p) => p.kind === "regular")
    .reduce((sum, p) => sum + Number(p.amount), 0);

  return (
    <section>
      <p className="back-link">
        <Link to="/contracts">← к списку договоров</Link>
      </p>
      <PageHeader
        title={`Договор ${contract.external_number ?? contract.id}`}
        description={
          <>
            <Badge value={contract.status} label={contractStatusLabel(contract.status)} /> · выдан{" "}
            {formatDate(contract.issued_at)}
            {contract.closed_at && ` · закрыт ${formatDate(contract.closed_at)}`}
            {contract.closure_reason && ` (${contract.closure_reason})`}
          </>
        }
        actions={
          contract.application_id ? (
            <Link className="btn" to={`/applications/${contract.application_id}`}>
              Открыть заявку
            </Link>
          ) : undefined
        }
      />
      {contract.closed_at && contract.closure_outcome === "unknown" && (
        <ErrorState message="Причина закрытия не распознана конфигом — таргет для этого договора не определён: ни хороший, ни плохой исход." />
      )}

      <div className="section-columns">
        <div className="section">
          <h2>Условия</h2>
          <dl className="kv-grid">
            <dt>Сумма товара</dt>
            <dd>{formatAmount(contract.principal)} ₽</dd>
            <dt>Первый взнос</dt>
            <dd>{formatAmount(contract.down_payment)} ₽</dd>
            <dt>Наценка</dt>
            <dd>{formatAmount(contract.markup)} ₽</dd>
            <dt>Итого к оплате</dt>
            <dd>
              {formatAmount(contract.total_amount)} ₽ на {contract.term_months} мес.
            </dd>
          </dl>
        </div>
        <div className="section">
          <h2>Состояние</h2>
          <dl className="kv-grid">
            <dt>Просрочка</dt>
            <dd>
              {delinquency ? (
                <span className={`badge ${delinquency.days_past_due > 0 ? "badge-danger" : "badge-success"}`}>
                  {delinquency.days_past_due > 0
                    ? `${delinquency.days_past_due} дн., ${formatAmount(delinquency.overdue_amount)} ₽`
                    : "нет"}
                </span>
              ) : (
                <span className="muted">ещё не рассчитана</span>
              )}
            </dd>
            <dt>На дату</dt>
            <dd>{delinquency ? formatDate(delinquency.as_of) : "—"}</dd>
            <dt>Оплачено по графику</dt>
            <dd>{formatAmount(paid)} ₽</dd>
            <dt>Платежей</dt>
            <dd>{contract.payments.length}</dd>
          </dl>
        </div>
      </div>

      <div className="section-columns">
        <div className="section">
          <h2>
            График платежей{" "}
            <span className="muted text-xs">
              {contract.schedule_source === "imported" ? "из выгрузки" : "сгенерирован равными платежами"}
            </span>
          </h2>
          {contract.schedule.length === 0 && <EmptyState title="Графика нет" />}
          {contract.schedule.length > 0 && (
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>№</th>
                  <th>Срок</th>
                  <th className="num">Сумма</th>
                </tr>
              </thead>
              <tbody>
                {contract.schedule.map((s) => (
                  <tr key={s.period_no}>
                    <td className="muted">{s.period_no}</td>
                    <td>{formatDate(s.due_date)}</td>
                    <td className="num">{formatAmount(s.due_amount)} ₽</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="section">
          <h2>Платежи</h2>
          {contract.payments.length === 0 && <EmptyState title="Платежей пока нет" />}
          {contract.payments.length > 0 && (
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th className="num">Сумма</th>
                  <th>Тип</th>
                  <th>Источник</th>
                </tr>
              </thead>
              <tbody>
                {contract.payments.map((p) => (
                  <tr key={p.id}>
                    <td>{formatDate(p.paid_at)}</td>
                    <td className="num">{formatAmount(p.amount)} ₽</td>
                    <td className={p.kind === "regular" ? "muted" : undefined}>
                      {PAYMENT_KIND_LABELS[p.kind] ?? p.kind}
                    </td>
                    <td className="muted">{PAYMENT_SOURCE_LABELS[p.source] ?? p.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
