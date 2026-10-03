import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { BarList } from "../components/BarList";
import { SourceBadge } from "../components/SourceBadge";
import { applicationStatusLabel, reasonCodeLabel } from "../lib/labels";

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
      <h1>Дашборд</h1>

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
      {query.isError && <p role="alert" className="alert">Не удалось загрузить дашборд.</p>}

      {query.data && (
        <div className="dashboard-grid">
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Воронка по состояниям</h2>
            <BarList
              emptyText="Нет заявок за период."
              items={query.data.funnel
                .slice()
                .sort((a, b) => b.count - a.count)
                .map((f) => ({ label: applicationStatusLabel(f.status), value: f.count }))}
            />
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Доля одобрений по неделям</h2>
            {query.data.weekly_approval.length === 0 && (
              <p className="muted">Нет решений стадии Б за период.</p>
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
            <h2 style={{ marginTop: 0 }}>Причины отказов</h2>
            <BarList
              emptyText="Отказов за период нет."
              items={query.data.rejection_reasons
                .slice()
                .sort((a, b) => b.count - a.count)
                .map((r) => ({ label: reasonCodeLabel(r.reason_code), value: r.count }))}
            />
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Ручные переопределения и расхождения</h2>
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
            <h2 style={{ marginTop: 0 }}>БКИ по месяцам</h2>
            {query.data.bki_monthly.length === 0 && <p className="muted">Запросов в БКИ за период нет.</p>}
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
            <h2 style={{ marginTop: 0 }}>Недоступность источников</h2>
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
            <h2 style={{ marginTop: 0 }}>Винтажный анализ (просрочка 60+ за 6 мес.)</h2>
            {query.data.vintage.length === 0 && <p className="muted">Нет выданных договоров.</p>}
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
            <h2 style={{ marginTop: 0 }}>Просрочка сейчас</h2>
            <BarList
              emptyText="Нет действующих договоров с данными по платежам."
              items={query.data.delinquency_buckets.map((b) => ({
                label: b.bucket === "0" ? "без просрочки" : `${b.bucket} дней`,
                value: b.contracts,
              }))}
            />
          </div>
        </div>
      )}
    </section>
  );
}
