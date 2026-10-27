import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, type BadgeTone } from "../components/Badge";
import { EmptyState, ErrorState, LoadingState } from "../components/ui";
import { contractStatusLabel, contractStatusTone } from "../lib/labels";
import { formatAmount, formatDate } from "../lib/format";

const PAYMENT_SOURCE_LABELS: Record<string, string> = { import: "выгрузка", manual: "вручную" };
const PAYMENT_KIND_LABELS: Record<string, string> = {
  regular: "по графику",
  penalty: "неустойка",
  reversal: "сторно",
};

// Строка графика: оплачена ли она нарастающим итогом оплаченного. Только
// для показа — просрочку (с льготными днями из конфига) считает сервер.
function periodState(cumulativeDue: number, dueAmount: number, paid: number, dueDate: string, today: string) {
  const covered = paid - (cumulativeDue - dueAmount);
  if (covered >= dueAmount) return { label: "оплачен", tone: "success" as BadgeTone };
  if (covered > 0) return { label: "частично", tone: "warning" as BadgeTone };
  if (dueDate < today) return { label: "не оплачен", tone: "danger" as BadgeTone };
  return { label: "впереди", tone: "neutral" as BadgeTone };
}

// Договор: сверху номер, состояние и четыре главные цифры (к оплате,
// оплачено, осталось, просрочка); ниже — график с отметкой по каждому
// платежу и сами платежи.
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
  const total = Number(contract.total_amount);
  const paid = Number(contract.paid_amount);
  const left = Math.max(0, total - paid);
  const share = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;
  const late = (delinquency?.days_past_due ?? 0) > 0;
  const today = new Date().toISOString().slice(0, 10);
  let cumulative = 0;

  return (
    <section className="ctr">
      <p className="back-link">
        <Link to="/contracts">← Договоры</Link>
      </p>

      <div className="ctr-head">
        <div className="ctr-head__main">
          <div className="ctr-head__title">
            <h1>Договор {contract.external_number ?? contract.id}</h1>
            <Badge
              value={contract.status}
              label={contractStatusLabel(contract.status)}
              tone={contractStatusTone(contract.status)}
            />
          </div>
          <p className="ctr-head__line">
            выдан {formatDate(contract.issued_at)}
            {contract.closed_at && ` · закрыт ${formatDate(contract.closed_at)}`}
            {contract.closure_reason && ` (${contract.closure_reason})`}
          </p>
          <p className="ctr-head__line ctr-head__line--muted">
            товар {formatAmount(contract.principal)} ₽ · взнос {formatAmount(contract.down_payment)} ₽ · наценка{" "}
            {formatAmount(contract.markup)} ₽
          </p>
        </div>
        {contract.application_id && (
          <Link className="btn" to={`/applications/${contract.application_id}`}>
            Открыть заявку
          </Link>
        )}

        <div className="ctr-metrics">
          <div className="ctr-metric">
            <div className="ctr-metric__label">К оплате</div>
            <div className="ctr-metric__value">{formatAmount(contract.total_amount)} ₽</div>
            <div className="ctr-metric__hint">на {contract.term_months} мес.</div>
          </div>
          <div className="ctr-metric">
            <div className="ctr-metric__label">Оплачено</div>
            <div className="ctr-metric__value">
              {formatAmount(contract.paid_amount)} ₽ <span className="ctr-metric__of">{share}%</span>
            </div>
            <div className="paid__bar" aria-hidden="true">
              <i style={{ width: `${share}%` }} />
            </div>
          </div>
          <div className="ctr-metric">
            <div className="ctr-metric__label">Осталось</div>
            <div className="ctr-metric__value">{formatAmount(left.toFixed(2))} ₽</div>
            <div className="ctr-metric__hint">платежей: {contract.payments.length}</div>
          </div>
          <div className="ctr-metric">
            <div className="ctr-metric__label">Просрочка</div>
            <div className={`ctr-metric__value ${late ? "ctr-metric__value--bad" : delinquency ? "ctr-metric__value--ok" : ""}`}>
              {!delinquency ? "—" : late ? `${delinquency.days_past_due} дн.` : "нет"}
            </div>
            <div className="ctr-metric__hint">
              {!delinquency
                ? "ещё не рассчитана"
                : late
                  ? `${formatAmount(delinquency.overdue_amount)} ₽ · на ${formatDate(delinquency.as_of)}`
                  : `на ${formatDate(delinquency.as_of)}`}
            </div>
          </div>
        </div>
      </div>

      {contract.closed_at && contract.closure_outcome === "unknown" && (
        <ErrorState message="Причина закрытия не распознана конфигом — таргет для этого договора не определён: ни хороший, ни плохой исход." />
      )}

      <div className="ctr-grid">
        <div className="pf-card ctr-card">
          <h2 className="pf-section-title">
            График <span className="muted">· {contract.schedule_source === "imported" ? "из выгрузки" : "равными платежами"}</span>
          </h2>
          {contract.schedule.length === 0 && <EmptyState title="Графика нет" />}
          {contract.schedule.length > 0 && (
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>№</th>
                  <th>Срок</th>
                  <th className="num">Сумма</th>
                  <th>Отметка</th>
                </tr>
              </thead>
              <tbody>
                {contract.schedule.map((s) => {
                  const amount = Number(s.due_amount);
                  cumulative += amount;
                  const state = periodState(cumulative, amount, paid, s.due_date, today);
                  return (
                    <tr key={s.period_no}>
                      <td className="muted">{s.period_no}</td>
                      <td>{formatDate(s.due_date)}</td>
                      <td className="num">{formatAmount(s.due_amount)} ₽</td>
                      <td>
                        <Badge value={state.label} label={state.label} tone={state.tone} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div className="pf-card ctr-card">
          <h2 className="pf-section-title">Платежи</h2>
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
                    <td className={p.kind === "reversal" ? "num late" : "num"}>
                      {p.kind === "reversal" && "−"}
                      {formatAmount(p.amount)} ₽
                    </td>
                    <td className={p.kind === "regular" ? "muted" : undefined}>{PAYMENT_KIND_LABELS[p.kind] ?? p.kind}</td>
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
