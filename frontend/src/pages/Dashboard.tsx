import { useState, type MouseEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { SourceBadge } from "../components/SourceBadge";
import { EmptyState, ErrorState, LoadingState, PageHeader, Segmented } from "../components/ui";
import { funnelStepLabel, reasonCodeLabel, sourceMeta } from "../lib/labels";
import { formatDate, formatMonth } from "../lib/format";
import type { components } from "../api/schema";

type Summary = components["schemas"]["DashboardSummary"];

// T23: «Аналитика» перестроена — сверху ряд ключевых цифр, ниже три
// тематических блока (заявки и решения → портфель → операционное). У каждого
// графика по-прежнему заголовок-вывод (T17, часть 5), данные и запрос те же.
// Графики — HTML/CSS без библиотек: столбики и полосы, подсказка при
// наведении (data-tip), цвета — токены index.css (--dq-* проверены
// валидатором палитры: один тон, светлота монотонна, обе темы).

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

type Preset = "30" | "90" | "182" | "custom";

function periodForDays(days: number): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - days);
  const toExclusive = new Date(to);
  toExclusive.setDate(toExclusive.getDate() + 1);
  return { from: isoDate(from), to: isoDate(toExclusive) };
}

function pct(numerator: number, denominator: number): string {
  if (denominator === 0) return "—";
  return `${Math.round((numerator / denominator) * 100)}%`;
}

function shortDate(iso: string): string {
  return formatDate(iso).slice(0, 5); // ДД.ММ
}

function Panel({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={wide ? "dash-panel dash-panel--wide" : "dash-panel"}>
      <h3 className="dash-panel__title">{title}</h3>
      {children}
    </section>
  );
}

function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: ReactNode; tone?: "bad" | "ok" }) {
  return (
    <div className="dash-kpi">
      <div className="dash-kpi__label">{label}</div>
      <div className={tone ? `dash-kpi__value dash-kpi__value--${tone}` : "dash-kpi__value"}>{value}</div>
      {hint && <div className="dash-kpi__hint">{hint}</div>}
    </div>
  );
}

// --- графики ------------------------------------------------------------------

function Funnel({ data }: { data: Summary }) {
  const submitted = data.funnel.find((f) => f.step === "submitted")?.count ?? 0;
  if (submitted === 0) return <EmptyState title="Нет заявок за период" />;
  return (
    <div className="funnel">
      {data.funnel.map((f, i) => {
        const prev = i > 0 ? data.funnel[i - 1].count : null;
        return (
          <div className="funnel__row" key={f.step}>
            <span className="funnel__label">{funnelStepLabel(f.step)}</span>
            <span className="bar-track funnel__track">
              <span
                className="bar-fill"
                style={{ width: `${(f.count / submitted) * 100}%` }}
                data-tip={`${funnelStepLabel(f.step)}: ${f.count} — ${pct(f.count, submitted)} от поданных`}
              />
            </span>
            <span className="funnel__value">
              <strong>{f.count}</strong> <span className="muted">{pct(f.count, submitted)}</span>
            </span>
            {/* Конверсия от предыдущего шага; больше 100% бывает, когда в период
                попали заявки, поданные раньше него, — тогда не показываем. */}
            <span className="funnel__step muted">
              {prev != null && prev > 0 && f.count <= prev ? `→ ${pct(f.count, prev)}` : ""}
            </span>
          </div>
        );
      })}
      <p className="dash-note">Справа — сколько дошло до шага от предыдущего.</p>
    </div>
  );
}

function WeeklyApproval({ data }: { data: Summary }) {
  const weeks = data.weekly_approval;
  if (weeks.length === 0) {
    return <EmptyState title="Пока нет решений стадии Б" hint="Появятся после первых «Рассмотреть» оператора." />;
  }
  const totalApproved = weeks.reduce((s, w) => s + w.approved, 0);
  const totalDecided = weeks.reduce((s, w) => s + w.total, 0);
  const average = totalDecided ? totalApproved / totalDecided : 0;
  // Текущая неделя ещё идёт — в «лучшая/худшая» не берём, как и недели
  // меньше чем с 5 решениями (доля по ним случайна).
  const weekAgo = new Date();
  weekAgo.setDate(weekAgo.getDate() - 7);
  const rated = weeks.filter((w) => w.total >= 5 && new Date(w.week_start) <= weekAgo);
  const best = rated.slice().sort((a, b) => b.approved / b.total - a.approved / a.total)[0];
  const worst = rated.slice().sort((a, b) => a.approved / a.total - b.approved / b.total)[0];
  return (
    <>
      <div className="colchart" role="img" aria-label="Доля одобрений стадии Б по неделям">
        <div className="colchart__avg" style={{ bottom: `${average * 100}%` }}>
          <span>в среднем {pct(totalApproved, totalDecided)}</span>
        </div>
        {weeks.map((w) => {
          const rate = w.total ? w.approved / w.total : 0;
          return (
            <div className="colchart__col" key={w.week_start}>
              <span className="colchart__value">{pct(w.approved, w.total)}</span>
              <span
                className={w.total < 5 ? "colchart__bar colchart__bar--thin" : "colchart__bar"}
                style={{ height: `${Math.max(rate * 100, 1)}%` }}
                data-tip={`Неделя с ${formatDate(w.week_start)}: одобрено ${w.approved} из ${w.total} (${pct(w.approved, w.total)})`}
              />
              <span className="colchart__x">{shortDate(w.week_start)}</span>
            </div>
          );
        })}
      </div>
      {best && worst && best !== worst && (
        <p className="dash-note">
          Лучшая неделя <span>с {formatDate(best.week_start)}</span> — {pct(best.approved, best.total)}, худшая{" "}
          <span>с {formatDate(worst.week_start)}</span> — {pct(worst.approved, worst.total)}. Бледные столбики — меньше
          5 решений за неделю, доля по ним случайна.
        </p>
      )}
    </>
  );
}

function RejectionReasons({ data }: { data: Summary }) {
  const sorted = data.rejection_reasons.slice().sort((a, b) => b.count - a.count);
  const total = sorted.reduce((s, r) => s + r.count, 0);
  if (total === 0) return <EmptyState title="Отказов за период нет" />;
  const max = sorted[0].count;
  return (
    <div className="hbars">
      {sorted.map((r) => (
        <div className="hbars__row" key={r.reason_code}>
          <span className="hbars__label">{reasonCodeLabel(r.reason_code)}</span>
          <span className="bar-track">
            <span
              className="bar-fill"
              style={{ width: `${(r.count / max) * 100}%` }}
              data-tip={`${reasonCodeLabel(r.reason_code)}: ${r.count} из ${total} отказов (${pct(r.count, total)})`}
            />
          </span>
          <span className="hbars__value">
            <strong>{r.count}</strong> <span className="muted">{pct(r.count, total)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function ScoreHistogram({ data }: { data: Summary }) {
  const buckets = data.score_distribution;
  const total = buckets.reduce((s, b) => s + b.count, 0);
  if (total === 0) return <EmptyState title="Нет решений с баллом БКИ за период" />;
  const t = data.score_thresholds;
  const reject = t?.auto_reject_below ?? null;
  const approve = t?.auto_approve_above ?? null;
  const zone = (start: number) =>
    reject != null && start + 50 <= reject ? "reject" : approve != null && start >= approve ? "approve" : "gray";
  const max = Math.max(...buckets.map((b) => b.count), 1);
  return (
    <>
      <div className="colchart colchart--hist" role="img" aria-label="Распределение балла БКИ">
        {buckets.map((b) => (
          <div className="colchart__col" key={b.bucket_start}>
            <span className="colchart__value">{b.count || ""}</span>
            <span
              className={`colchart__bar colchart__bar--${zone(b.bucket_start)}`}
              style={{ height: `${Math.max((b.count / max) * 100, b.count ? 2 : 0)}%` }}
              data-tip={`Балл ${b.bucket_start}–${b.bucket_start + 49}: ${b.count} ${b.count === 1 ? "решение" : "решений"}`}
            />
            <span className="colchart__x">{b.bucket_start}</span>
          </div>
        ))}
      </div>
      <div className="legend">
        {reject != null && (
          <span className="legend__item legend__item--reject">отказ — ниже {Math.round(reject)}</span>
        )}
        <span className="legend__item legend__item--gray">серая зона</span>
        {approve != null && (
          <span className="legend__item legend__item--approve">одобрение — от {Math.round(approve)}</span>
        )}
      </div>
      <p className="dash-note">
        Пороги — действующей версии правил, без поправок матрицы по сумме сделки: ориентир по портфелю, а не порог
        конкретного решения.
      </p>
    </>
  );
}

const DQ_LABELS: Record<string, string> = {
  "0": "без просрочки",
  "1-29": "1–29 дней",
  "30-59": "30–59 дней",
  "60-89": "60–89 дней",
  "90+": "90+ дней",
};
const DQ_ORDER = ["0", "1-29", "30-59", "60-89", "90+"];

function Delinquency({ data }: { data: Summary }) {
  const buckets = DQ_ORDER.map((key) => ({
    key,
    contracts: data.delinquency_buckets.find((b) => b.bucket === key)?.contracts ?? 0,
  }));
  const total = buckets.reduce((s, b) => s + b.contracts, 0);
  if (total === 0) {
    return <EmptyState title="Нет действующих договоров" hint="Появятся после импорта договоров и платежей." />;
  }
  return (
    <>
      <div className="stackbar" role="img" aria-label="Действующие договоры по сроку просрочки">
        {buckets
          .filter((b) => b.contracts > 0)
          .map((b, i) => (
            <span
              key={b.key}
              className={`stackbar__seg stackbar__seg--dq${DQ_ORDER.indexOf(b.key)}`}
              style={{ flexGrow: b.contracts }}
              data-tip={`${DQ_LABELS[b.key]}: ${b.contracts} (${pct(b.contracts, total)})`}
              aria-hidden={i > 0}
            />
          ))}
      </div>
      <ul className="dq-legend">
        {buckets.map((b, i) => (
          <li key={b.key}>
            <span className={`dq-swatch stackbar__seg--dq${i}`} aria-hidden="true" />
            <span className="dq-legend__label">{DQ_LABELS[b.key]}</span>
            <strong>{b.contracts}</strong>
            <span className="muted">{pct(b.contracts, total)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Vintage({ data }: { data: Summary }) {
  if (data.vintage.length === 0) {
    return <EmptyState title="Договоров ещё нет" hint="Появится после первых выданных договоров." />;
  }
  return (
    <table className="table table--compact">
      <thead>
        <tr>
          <th>Месяц выдачи</th>
          <th className="num">Договоров</th>
          <th className="num">Зрелых</th>
          <th>Доля плохих (60+ за 6 мес.)</th>
        </tr>
      </thead>
      <tbody>
        {data.vintage.map((v) => {
          const rate = v.mature_contracts ? (v.bad_contracts ?? 0) / v.mature_contracts : null;
          return (
            <tr key={v.cohort_month}>
              <td>{formatMonth(v.cohort_month)}</td>
              <td className="num">{v.total_contracts}</td>
              <td className="num">{v.mature_contracts}</td>
              <td>
                {rate == null ? (
                  <span className="muted">рано оценивать</span>
                ) : (
                  <span className="meter-cell">
                    <span className="meter">
                      <span className="meter__fill" style={{ width: `${rate * 100}%` }} />
                    </span>
                    {pct(v.bad_contracts ?? 0, v.mature_contracts)}{" "}
                    <span className="muted">
                      ({v.bad_contracts} из {v.mature_contracts})
                    </span>
                  </span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// Одна подсказка на экран: читает data-tip у элемента под курсором (полоски
// лежат в контейнерах с overflow: hidden — CSS ::after там обрезался бы).
function ChartTooltip({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null);
  function onMove(event: MouseEvent<HTMLDivElement>) {
    const target = (event.target as HTMLElement).closest<HTMLElement>("[data-tip]");
    setTip(target ? { text: target.dataset.tip ?? "", x: event.clientX, y: event.clientY } : null);
  }
  return (
    <div onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
      {children}
      {tip && (
        <div className="chart-tip" role="tooltip" style={{ left: tip.x + 12, top: tip.y + 14 }}>
          {tip.text}
        </div>
      )}
    </div>
  );
}

// --- экран --------------------------------------------------------------------

export function DashboardPage() {
  const [preset, setPreset] = useState<Preset>("90");
  const initial = periodForDays(90);
  const [periodFrom, setPeriodFrom] = useState(initial.from);
  const [periodTo, setPeriodTo] = useState(initial.to);

  function choosePreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      const period = periodForDays(Number(next));
      setPeriodFrom(period.from);
      setPeriodTo(period.to);
    }
  }

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

  const d = query.data;

  return (
    <section className="dashboard">
      <PageHeader
        title="Аналитика"
        description="Как идут заявки, почему отказываем и как платит портфель — за выбранный период. Недели и месяцы — по московскому времени."
      />

      <div className="dash-period">
        <Segmented
          label="Период"
          value={preset}
          onChange={choosePreset}
          options={[
            { value: "30", label: "30 дней" },
            { value: "90", label: "90 дней" },
            { value: "182", label: "Полгода" },
            { value: "custom", label: "Свой период" },
          ]}
        />
        <label className="dash-date">
          с
          <input
            type="date"
            lang="ru"
            value={periodFrom}
            onChange={(e) => {
              setPreset("custom");
              setPeriodFrom(e.target.value);
            }}
          />
          {/* Формат нативного календаря зависит от локали ОС — подпись рядом
              всегда ДД.ММ.ГГГГ, тем же Intl, что в карточке. */}
          <span className="muted">{formatDate(periodFrom)}</span>
        </label>
        <label className="dash-date">
          по (не включая)
          <input
            type="date"
            lang="ru"
            value={periodTo}
            onChange={(e) => {
              setPreset("custom");
              setPeriodTo(e.target.value);
            }}
          />
          <span className="muted">{formatDate(periodTo)}</span>
        </label>
      </div>

      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить аналитику." onRetry={() => void query.refetch()} />}

      {d && (
        <ChartTooltip>
          <DashboardBody d={d} />
        </ChartTooltip>
      )}
    </section>
  );
}

function DashboardBody({ d }: { d: Summary }) {
  const submitted = d.funnel.find((f) => f.step === "submitted")?.count ?? 0;
  const approved = d.funnel.find((f) => f.step === "approved")?.count ?? 0;
  const weeklyApproved = d.weekly_approval.reduce((s, w) => s + w.approved, 0);
  const weeklyDecided = d.weekly_approval.reduce((s, w) => s + w.total, 0);
  const reasons = d.rejection_reasons.slice().sort((a, b) => b.count - a.count);
  const rejectedTotal = reasons.reduce((s, r) => s + r.count, 0);
  const activeContracts = d.delinquency_buckets.reduce((s, b) => s + b.contracts, 0);
  const overdue = d.delinquency_buckets.filter((b) => b.bucket !== "0").reduce((s, b) => s + b.contracts, 0);
  const severe = d.delinquency_buckets.filter((b) => b.bucket === "90+").reduce((s, b) => s + b.contracts, 0);
  const matured = d.vintage.filter((v) => v.mature_contracts > 0);
  const totalMature = matured.reduce((s, v) => s + v.mature_contracts, 0);
  const totalBad = matured.reduce((s, v) => s + (v.bad_contracts ?? 0), 0);
  const attempts = d.bki_monthly.reduce((s, m) => s + m.attempts, 0);
  const successful = d.bki_monthly.reduce((s, m) => s + m.successful, 0);
  const threshold = d.source_unavailable_warn_threshold;
  const worstSource = d.source_availability
    .slice()
    .sort((a, b) => b.unavailable / Math.max(1, b.total) - a.unavailable / Math.max(1, a.total))
    .find((s) => s.total > 0 && s.unavailable / s.total >= threshold);

  return (
    <>
      <div className="dash-kpis">
        <Kpi label="Заявок за период" value={String(submitted)} hint={`дошли до решения стадии Б: ${weeklyDecided}`} />
        <Kpi label="Одобрено" value={String(approved)} hint={`${pct(approved, submitted)} от поданных`} tone="ok" />
        <Kpi
          label="Доля одобрений стадии Б"
          value={pct(weeklyApproved, weeklyDecided)}
          hint={`${weeklyApproved} из ${weeklyDecided} решений`}
        />
        <Kpi
          label="Отказов"
          value={String(rejectedTotal)}
          hint={reasons[0] ? `чаще всего — «${reasonCodeLabel(reasons[0].reason_code)}»` : "нет"}
        />
        <Kpi
          label="Договоров с просрочкой"
          value={pct(overdue, activeContracts)}
          hint={`${overdue} из ${activeContracts} действующих · 90+: ${severe}`}
          tone={overdue > 0 ? "bad" : undefined}
        />
      </div>

      <h2 className="dash-section">Заявки и решения</h2>
      <div className="dash-grid">
        <Panel
          title={submitted === 0 ? "Воронка заявок" : `До одобрения доходит ${pct(approved, submitted)} поданных заявок`}
        >
          <Funnel data={d} />
        </Panel>
        <Panel
          title={
            weeklyDecided === 0
              ? "Доля одобрений по неделям"
              : `За период одобрено ${pct(weeklyApproved, weeklyDecided)} решений стадии Б`
          }
        >
          <WeeklyApproval data={d} />
        </Panel>
        <Panel
          title={
            reasons[0]
              ? `Чаще всего отказывают по причине «${reasonCodeLabel(reasons[0].reason_code)}» (${pct(reasons[0].count, rejectedTotal)})`
              : "Причины отказов"
          }
        >
          <RejectionReasons data={d} />
        </Panel>
        <Panel title="Балл БКИ по решениям стадии Б">
          <ScoreHistogram data={d} />
        </Panel>
      </div>

      <h2 className="dash-section">Портфель</h2>
      <div className="dash-grid">
        <Panel
          title={
            activeContracts === 0
              ? "Просрочка сейчас"
              : `С просрочкой сейчас ${pct(overdue, activeContracts)} действующих договоров`
          }
        >
          <Delinquency data={d} />
        </Panel>
        <Panel
          title={
            totalMature === 0
              ? "Просрочка по месяцу выдачи (60+ за 6 мес.)"
              : `Среди зрелых когорт просрочку 60+ за 6 мес. допускают ${pct(totalBad, totalMature)} договоров`
          }
        >
          <Vintage data={d} />
        </Panel>
      </div>

      <h2 className="dash-section">Операционное</h2>
      <div className="dash-grid dash-grid--3">
        <Panel
          title={
            d.overrides.applications_total === 0
              ? "Ручные переопределения"
              : `Оператор меняет решение системы в ${pct(d.overrides.overridden, d.overrides.applications_total)} заявок`
          }
        >
          {d.overrides.applications_total === 0 ? (
            <EmptyState title="Заявок за период нет" />
          ) : (
            <div className="dash-stats">
              <div>
                <div className="dash-stat__value">{pct(d.overrides.overridden, d.overrides.applications_total)}</div>
                <div className="dash-stat__label">
                  переопределено оператором ({d.overrides.overridden} из {d.overrides.applications_total})
                </div>
              </div>
              <div>
                <div className="dash-stat__value">{pct(d.overrides.discrepancies, d.overrides.overridden)}</div>
                <div className="dash-stat__label">
                  {/* Доля от ПЕРЕОПРЕДЕЛЁННЫХ, не от всех заявок (ваше замечание T17). */}
                  из них разошлись с системой ({d.overrides.discrepancies} из {d.overrides.overridden})
                </div>
              </div>
            </div>
          )}
        </Panel>

        <Panel
          title={
            d.source_availability.length === 0
              ? "Недоступность источников"
              : !worstSource
                ? "Источники работают стабильно"
                : `Чаще всего недоступен источник «${sourceMeta(worstSource.source).label}» (${pct(worstSource.unavailable, worstSource.total)})`
          }
        >
          {d.source_availability.length === 0 ? (
            <EmptyState title="Проверок за период нет" />
          ) : (
            <ul className="source-list">
              {d.source_availability.map((s) => (
                <li key={s.source}>
                  <SourceBadge source={s.source} />
                  <span className="muted">
                    {s.unavailable === 0 ? "без сбоев" : `недоступен ${s.unavailable} раз`} · {s.total} проверок
                  </span>
                  <strong className={s.unavailable / Math.max(1, s.total) >= threshold ? "text-bad" : undefined}>
                    {pct(s.unavailable, s.total)}
                  </strong>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title={attempts === 0 ? "БКИ по месяцам" : `${pct(successful, attempts)} платных запросов в БКИ возвращают отчёт`}>
          {d.bki_monthly.length === 0 ? (
            <EmptyState title="Запросов в БКИ за период нет" />
          ) : (
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Месяц</th>
                  <th className="num">Платных запросов</th>
                  <th className="num">С отчётом</th>
                </tr>
              </thead>
              <tbody>
                {d.bki_monthly.map((m) => (
                  <tr key={m.month}>
                    <td>{formatMonth(m.month)}</td>
                    <td className="num">{m.attempts}</td>
                    <td className="num">{m.successful}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </>
  );
}
