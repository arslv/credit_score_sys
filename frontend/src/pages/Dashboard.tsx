import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { BarList } from "../components/BarList";
import { SourceBadge } from "../components/SourceBadge";
import { Term } from "../components/Term";
import { funnelStepLabel, reasonCodeLabel, sourceMeta } from "../lib/labels";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultPeriod(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - 90);
  const toExclusive = new Date(to);
  toExclusive.setDate(toExclusive.getDate() + 1);
  return { from: isoDate(from), to: isoDate(toExclusive) };
}

function pct(numerator: number, denominator: number): string {
  if (denominator === 0) return "—";
  return `${Math.round((numerator / denominator) * 100)}%`;
}

// T17, часть 5 (ваше указание): у каждого графика — заголовок-вывод, не
// нейтральное название блока. Выводы считаются ниже, из уже пришедших чисел —
// порогов или констант из конфига тут нет, только арифметика сравнения.

export function DashboardPage() {
  const initial = defaultPeriod();
  const [periodFrom, setPeriodFrom] = useState(initial.from);
  const [periodTo, setPeriodTo] = useState(initial.to);

  const query = useQuery({
    queryKey: ["dashboard", periodFrom, periodTo],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/dashboard/summary", {
        params: { query: { period_from: periodFrom || undefined, period_to: periodTo || undefined } },
      });
      if (error) throw error;
      return data;
    },
  });

  return (
    <section>
      <h1>Аналитика</h1>

      <div className="toolbar">
        <label>
          С
          <input type="date" value={periodFrom} onChange={(e) => setPeriodFrom(e.target.value)} />
        </label>
        <label>
          По (не включая)
          <input type="date" value={periodTo} onChange={(e) => setPeriodTo(e.target.value)} />
        </label>
        <span className="muted">Недели и месяцы — по московскому времени.</span>
      </div>

      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить аналитику.</p>}

      {query.data && (
        <div className="dashboard-grid">
          <div className="card">
            {(() => {
              const d = query.data!;
              const submitted = d.funnel.find((f) => f.step === "submitted")?.count ?? 0;
              const approved = d.funnel.find((f) => f.step === "approved")?.count ?? 0;
              return (
                <>
                  <h2 style={{ marginTop: 0 }}>
                    {submitted === 0
                      ? "Воронка заявок"
                      : `До одобрения доходит ${pct(approved, submitted)} поданных заявок`}
                  </h2>
                  <BarList
                    emptyText="Нет заявок за период."
                    items={d.funnel.map((f) => ({
                      label: funnelStepLabel(f.step),
                      value: f.count,
                      displayValue: `${f.count} (${pct(f.count, submitted)})`,
                    }))}
                  />
                </>
              );
            })()}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const totalApproved = d.weekly_approval.reduce((s, w) => s + w.approved, 0);
              const totalDecided = d.weekly_approval.reduce((s, w) => s + w.total, 0);
              return (
                <h2 style={{ marginTop: 0 }}>
                  {totalDecided === 0
                    ? "Доля одобрений по неделям"
                    : `За период одобрено ${pct(totalApproved, totalDecided)} решений стадии Б`}
                </h2>
              );
            })()}
            {query.data.weekly_approval.length === 0 && (
              <p className="muted">
                Пока нет решений стадии Б за период — появятся после того, как оператор начнёт нажимать
                «Рассмотреть».
              </p>
            )}
            {query.data.weekly_approval.length > 0 && (
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Неделя с</th>
                    <th>Одобрено</th>
                    <th>Всего решений</th>
                    <th>Доля</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.weekly_approval.map((w) => (
                    <tr key={w.week_start}>
                      <td>{w.week_start}</td>
                      <td>{w.approved}</td>
                      <td>{w.total}</td>
                      <td>{pct(w.approved, w.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const sorted = d.rejection_reasons.slice().sort((a, b) => b.count - a.count);
              const totalRejected = sorted.reduce((s, r) => s + r.count, 0);
              const top = sorted[0];
              return (
                <>
                  <h2 style={{ marginTop: 0 }}>
                    {!top
                      ? "Причины отказов"
                      : `Чаще всего отказывают по причине «${reasonCodeLabel(top.reason_code)}» (${pct(top.count, totalRejected)})`}
                  </h2>
                  <BarList
                    emptyText="Отказов за период нет."
                    items={sorted.map((r) => ({ label: reasonCodeLabel(r.reason_code), value: r.count }))}
                  />
                </>
              );
            })()}
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>
              {query.data.overrides.applications_total === 0
                ? "Ручные переопределения и расхождения"
                : `Оператор меняет решение системы в ${pct(query.data.overrides.overridden, query.data.overrides.applications_total)} заявок`}
            </h2>
            {query.data.overrides.applications_total === 0 ? (
              <p className="muted">Заявок за период нет.</p>
            ) : (
              <div className="stat-row">
                <div className="stat">
                  <span className="stat-value">
                    {pct(query.data.overrides.overridden, query.data.overrides.applications_total)}
                  </span>
                  <span className="stat-label">
                    переопределено оператором ({query.data.overrides.overridden} из{" "}
                    {query.data.overrides.applications_total})
                  </span>
                </div>
                <div className="stat">
                  <span className="stat-value">
                    {pct(query.data.overrides.discrepancies, query.data.overrides.applications_total)}
                  </span>
                  <span className="stat-label">
                    разошлись с системой ({query.data.overrides.discrepancies} из{" "}
                    {query.data.overrides.applications_total})
                  </span>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const attempts = d.bki_monthly.reduce((s, m) => s + m.attempts, 0);
              const successful = d.bki_monthly.reduce((s, m) => s + m.successful, 0);
              return (
                <h2 style={{ marginTop: 0 }}>
                  {attempts === 0 ? "БКИ по месяцам" : `${pct(successful, attempts)} платных запросов в БКИ возвращают отчёт`}
                </h2>
              );
            })()}
            {query.data.bki_monthly.length === 0 && (
              <p className="muted">Запросов в БКИ за период нет — появятся после первых решений стадии Б.</p>
            )}
            {query.data.bki_monthly.length > 0 && (
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Месяц</th>
                    <th>Платных попыток</th>
                    <th>Успешных отчётов</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.bki_monthly.map((m) => (
                    <tr key={m.month}>
                      <td>{m.month}</td>
                      <td>{m.attempts}</td>
                      <td>{m.successful}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const sorted = d.source_availability.slice().sort((a, b) => b.unavailable / Math.max(1, b.total) - a.unavailable / Math.max(1, a.total));
              const worst = sorted.find((s) => s.unavailable > 0);
              return (
                <h2 style={{ marginTop: 0 }}>
                  {!worst
                    ? "Недоступность источников"
                    : `Чаще всего недоступен источник «${sourceMeta(worst.source).label}» (${pct(worst.unavailable, worst.total)})`}
                </h2>
              );
            })()}
            {query.data.source_availability.length === 0 && <p className="muted">Проверок за период нет.</p>}
            {query.data.source_availability.length > 0 && (
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Источник</th>
                    <th>Недоступен</th>
                    <th>Всего</th>
                    <th>Доля</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.source_availability.map((s) => (
                    <tr key={s.source}>
                      <td>
                        <SourceBadge source={s.source} />
                      </td>
                      <td>{s.unavailable}</td>
                      <td>{s.total}</td>
                      <td>{pct(s.unavailable, s.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const mature = d.score_distribution.reduce((s, b) => s + b.count, 0);
              const t = d.score_thresholds;
              return (
                <>
                  <h2 style={{ marginTop: 0 }}>
                    {mature === 0 ? "Балл БКИ" : "Распределение балла БКИ по решениям стадии Б"}
                  </h2>
                  {t && (t.auto_approve_above != null || t.auto_reject_below != null) && (
                    <p className="muted" style={{ marginTop: -8 }}>
                      <Term tip="Пороги действующей версии правил, без учёта матрицы по сумме сделки — ориентир по всему портфелю, не порог конкретного решения.">
                        Пороги сейчас
                      </Term>
                      {t.auto_reject_below != null && <>: отказ ниже {Math.round(t.auto_reject_below)}</>}
                      {t.auto_approve_above != null && <>, одобрение выше {Math.round(t.auto_approve_above)}</>}
                    </p>
                  )}
                  <BarList
                    emptyText="Нет решений стадии Б с баллом БКИ за период."
                    items={d.score_distribution.map((b) => ({
                      label: `${b.bucket_start}–${b.bucket_start + 49}`,
                      value: b.count,
                    }))}
                  />
                </>
              );
            })()}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const matured = d.vintage.filter((v) => v.mature_contracts > 0);
              const totalMature = matured.reduce((s, v) => s + v.mature_contracts, 0);
              const totalBad = matured.reduce((s, v) => s + (v.bad_contracts ?? 0), 0);
              return (
                <h2 style={{ marginTop: 0 }}>
                  {totalMature === 0
                    ? "Винтажный анализ (просрочка 60+ за 6 мес.)"
                    : `Среди зрелых когорт просрочку 60+ за 6 мес. допускают ${pct(totalBad, totalMature)} договоров`}
                </h2>
              );
            })()}
            {query.data.vintage.length === 0 && (
              <p className="muted">Появится после первых выданных договоров («Портфель» → «Договоры»).</p>
            )}
            {query.data.vintage.length > 0 && (
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Когорта</th>
                    <th>Договоров</th>
                    <th>Зрелых</th>
                    <th>Доля плохих</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.vintage.map((v) => (
                    <tr key={v.cohort_month}>
                      <td>{v.cohort_month}</td>
                      <td>{v.total_contracts}</td>
                      <td>{v.mature_contracts}</td>
                      <td>
                        {v.mature_contracts === 0
                          ? "ещё не зрела"
                          : `${pct(v.bad_contracts ?? 0, v.mature_contracts)} (${v.bad_contracts} из ${v.mature_contracts})`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            {(() => {
              const d = query.data!;
              const total = d.delinquency_buckets.reduce((s, b) => s + b.contracts, 0);
              const overdue = d.delinquency_buckets.filter((b) => b.bucket !== "0").reduce((s, b) => s + b.contracts, 0);
              return (
                <h2 style={{ marginTop: 0 }}>
                  {total === 0 ? "Просрочка сейчас" : `С просрочкой сейчас ${pct(overdue, total)} действующих договоров`}
                </h2>
              );
            })()}
            {query.data.delinquency_buckets.length === 0 && (
              <p className="muted">Появится после импорта платежей («Портфель» → «Импорт»).</p>
            )}
            {query.data.delinquency_buckets.length > 0 && (
              <BarList
                emptyText="Нет действующих договоров с данными по платежам."
                items={query.data.delinquency_buckets.map((b) => ({
                  label: b.bucket === "0" ? "без просрочки" : `${b.bucket} дней`,
                  value: b.contracts,
                }))}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
