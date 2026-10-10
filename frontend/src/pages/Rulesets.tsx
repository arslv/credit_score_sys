import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import * as yaml from "js-yaml";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

// Ответы этих эндпоинтов не типизированы в схеме (обычные dict, не Pydantic
// response_model — не было смысла заводить отдельные схемы под каждый ответ
// этого экрана) — формы описаны здесь локально и соответствуют
// app/api/rulesets.py построчно.

interface ApplicationRow {
  application_id: string;
  external_id: string;
  client_name: string | null;
  active_outcome: string;
  active_reason_code: string | null;
  candidate_outcome: string;
  candidate_reason_code: string | null;
  actual_decision_outcome: string | null;
  actual_decision_reason_code: string | null;
}

interface NotComputableRule {
  rule_id: string;
  count: number;
  application_ids: string[];
}

interface SimulationSummary {
  sample_size_requested: number;
  sample_size_actual: number;
  changed_count: number;
  approval_rate_active: number | null;
  approval_rate_candidate: number | null;
  known_denominator: number;
  swap_in: ApplicationRow[];
  swap_out: ApplicationRow[];
  unknown_after_stage_a_pass: ApplicationRow[];
  unknown_after_stage_a_pass_total: number;
  insufficient_data_count: number;
  not_computable: NotComputableRule[];
  defaultness_by_known_outcome:
    | { available: false; note: string }
    | {
        available: true;
        by_active_outcome: Record<string, { known: number; bad: number; bad_rate: number }>;
        by_candidate_outcome: Record<string, { known: number; bad: number; bad_rate: number }>;
      };
}

interface SimulateResponse {
  simulation_id: string;
  active_version: number;
  expires_at: string;
  summary: SimulationSummary;
}

interface VersionRow {
  id: string;
  version: number;
  comment: string | null;
  author_id: string | null;
  effective_from: string;
  created_at: string;
  is_active: boolean;
  changes: string[];
}

interface VersionsResponse {
  versions: VersionRow[];
  file_conflict: { conflict: boolean; file_valid: boolean; error: string | null };
}

const OUTCOME_LABELS: Record<string, string> = {
  rejected_a: "отказ (стадия А)",
  rejected_b: "отказ (стадия Б)",
  manual_review: "серая зона",
  approved: "одобрено",
  no_data: "нет данных",
};

function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

function formatRate(rate: number | null): string {
  return rate == null ? "—" : `${(rate * 100).toFixed(1)}%`;
}

// Склонение «заявка» — два разных ряда форм, т.к. русский требует разного
// падежа в зависимости от позиции в предложении (ваше замечание: «для 2
// заявок», «для 1 заявки» — после предлога ВСЕГДА родительный падеж, и там
// единственное число только при 1/21/31..., а не 1-4 как в именительном).
function pluralForCount(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

// Именительный, считая: «1 заявка», «2 заявки», «5 заявок».
function countWord(n: number): string {
  return pluralForCount(n, "заявка", "заявки", "заявок");
}

// Родительный после предлога («для»/«из»): «для 1 заявки», «для 2 заявок».
function ofWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  return mod10 === 1 && mod100 !== 11 ? "заявки" : "заявок";
}

// Типовые пороги — плоские скаляры из ТЗ («Правила и пороги»: форма для
// типовых порогов + YAML-редактор для остального). Правит только эти поля в
// уже загруженном YAML напрямую (js-yaml), а не отдельную структуру —
// текстовый редактор всегда остаётся единственным источником правды перед
// отправкой на бэкенд. percent: true — значение в YAML хранится как доля
// (0.6), а в форме показывается и вводится как проценты (60) — ваше замечание
// про «0,6» и про то, что порог ручной проверки по ПДН нигде не было видно
// кроме YAML.
interface QuickField {
  key: string;
  label: string;
  section: "thresholds" | "limits" | "pti";
  help: string;
  unit: string;
  min: number;
  max: number;
  percent?: boolean;
  get: (config: any) => unknown;
  set: (config: any, value: number) => void;
}

function findRule(config: any, ruleId: string): any {
  return (config?.soft_rules ?? []).find((r: any) => r?.id === ruleId);
}

// Поля формы «Скоринг». Подписи и подсказки — здесь, а не в YAML: риск-менеджер
// видит, что именно регулирует каждый порог. Границы — только ввод (не
// бизнес-правило): значения вне диапазона не уйдут в публикацию. Инварианты
// между полями (отказ ниже одобрения) проверяются отдельно ниже по тексту.
const QUICK_FIELDS: QuickField[] = [
  {
    key: "auto_approve_above", label: "Автоодобрение выше", section: "thresholds", unit: "балл",
    help: "Балл БКИ, выше которого заявка одобряется автоматически (при отсутствии стоп-факторов и пройденной ФССП). Для сделок дороже порога матрицы может действовать свой порог.",
    min: 0, max: 999,
    get: (c) => c?.score_thresholds?.auto_approve_above, set: (c, v) => { c.score_thresholds.auto_approve_above = v; },
  },
  {
    key: "auto_reject_below", label: "Автоотказ ниже", section: "thresholds", unit: "балл",
    help: "Балл БКИ, ниже которого отказ без ручного рассмотрения. Между отказом и одобрением — серая зона, решение за человеком.",
    min: 0, max: 999,
    get: (c) => c?.score_thresholds?.auto_reject_below, set: (c, v) => { c.score_thresholds.auto_reject_below = v; },
  },
  {
    key: "amount_min", label: "Мин. сумма", section: "limits", unit: "₽",
    help: "Минимальная сумма сделки, которую система рассматривает.",
    min: 0, max: 100_000_000,
    get: (c) => c?.limits?.amount_min, set: (c, v) => { c.limits.amount_min = v; },
  },
  {
    key: "amount_max", label: "Макс. сумма", section: "limits", unit: "₽",
    help: "Максимальная сумма сделки. Выше — заявка не рассматривается автоматически.",
    min: 0, max: 100_000_000,
    get: (c) => c?.limits?.amount_max, set: (c, v) => { c.limits.amount_max = v; },
  },
  {
    key: "term_min", label: "Мин. срок, мес.", section: "limits", unit: "мес.",
    help: "Минимальный срок рассрочки в месяцах.",
    min: 1, max: 120,
    get: (c) => c?.limits?.term_min, set: (c, v) => { c.limits.term_min = v; },
  },
  {
    key: "term_max", label: "Макс. срок, мес.", section: "limits", unit: "мес.",
    help: "Максимальный срок рассрочки в месяцах.",
    min: 1, max: 120,
    get: (c) => c?.limits?.term_max, set: (c, v) => { c.limits.term_max = v; },
  },
  {
    key: "pti_max", label: "Долговая нагрузка: отказ выше", percent: true, section: "pti", unit: "%",
    help: "ПДН — доля дохода, которая уходит на платежи по долгам. Выше этого значения — отказ.",
    min: 0, max: 100,
    get: (c) => c?.limits?.pti_max, set: (c, v) => { c.limits.pti_max = v; },
  },
  {
    key: "pti_high", label: "Долговая нагрузка: ручная проверка выше", percent: true, section: "pti", unit: "%",
    help: "ПДН выше этого значения, но не выше порога отказа — заявка уходит на ручную проверку.",
    min: 0, max: 100,
    get: (c) => findRule(c, "pti_high")?.value,
    set: (c, v) => { const r = findRule(c, "pti_high"); if (r) r.value = v; },
  },
];

const SECTION_TITLES: Record<QuickField["section"], string> = {
  thresholds: "Пороги решения (балл БКИ)",
  limits: "Лимиты сделки",
  pti: "Долговая нагрузка (ПДН)",
};

function ApplicationRowView({ row }: { row: ApplicationRow }) {
  return (
    <tr>
      <td>
        <Link to={`/applications/${row.application_id}`}>{row.external_id}</Link>
      </td>
      <td>{row.client_name ?? "—"}</td>
      <td>
        {outcomeLabel(row.active_outcome)}
        {row.active_reason_code && <span className="muted"> ({row.active_reason_code})</span>}
      </td>
      <td>
        {outcomeLabel(row.candidate_outcome)}
        {row.candidate_reason_code && <span className="muted"> ({row.candidate_reason_code})</span>}
      </td>
      <td className="muted">
        {row.actual_decision_outcome ? outcomeLabel(row.actual_decision_outcome) : "—"}
        {row.actual_decision_reason_code && ` (${row.actual_decision_reason_code})`}
      </td>
    </tr>
  );
}

function ApplicationsTable({ title, rows, emptyText }: { title: string; rows: ApplicationRow[]; emptyText: string }) {
  return (
    <div className="stack">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Заявка</th>
              <th>Клиент</th>
              <th>Сейчас</th>
              <th>По новому конфигу</th>
              <th>Фактическое решение (справочно)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <ApplicationRowView key={r.application_id} row={r} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function validateQuickField(f: QuickField, config: any): string | null {
  const raw = f.get(config);
  // В YAML числа иногда лежат строками ("0.6") — принимаем любое число-подобное значение.
  const numeric = raw == null || raw === "" ? NaN : Number(raw);
  if (Number.isNaN(numeric)) return "значение не задано";
  const shown = f.percent ? Math.round(numeric * 1000) / 10 : numeric;
  if (shown < f.min || shown > f.max) return `допустимо от ${f.min} до ${f.max} ${f.unit}`;
  return null;
}

export function RulesetsPage() {
  const queryClient = useQueryClient();
  const [yamlText, setYamlText] = useState("");
  const [simulation, setSimulation] = useState<SimulateResponse | null>(null);
  const [simulateError, setSimulateError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [publishComment, setPublishComment] = useState("");

  // Симуляция относится к конкретному тексту конфига: любая правка сбрасывает её,
  // иначе «Опубликовать» отправило бы устаревший прогон (бэкенд всё равно отклонит).
  useEffect(() => {
    setSimulation(null);
  }, [yamlText]);

  const activeQuery = useQuery({
    queryKey: ["ruleset-active"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/rulesets/active");
      if (response.status === 403) throw new Error("FORBIDDEN");
      if (error) throw error;
      return data as unknown as { version: number; comment: string | null; effective_from: string; yaml: string };
    },
    retry: false,
  });

  useEffect(() => {
    if (activeQuery.data && !yamlText) setYamlText(activeQuery.data.yaml);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeQuery.data]);

  const versionsQuery = useQuery({
    queryKey: ["ruleset-versions"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/rulesets/versions");
      if (error) throw error;
      return data as unknown as VersionsResponse;
    },
  });

  let parsed: any = null;
  let parseError: string | null = null;
  try {
    parsed = yaml.load(yamlText);
  } catch (e) {
    parseError = e instanceof Error ? e.message : String(e);
  }

  const fieldErrors: Record<string, string | null> = {};
  for (const f of QUICK_FIELDS) fieldErrors[f.key] = parsed ? validateQuickField(f, parsed) : null;
  const rejectBelow = parsed?.score_thresholds?.auto_reject_below;
  const approveAbove = parsed?.score_thresholds?.auto_approve_above;
  const orderError =
    typeof rejectBelow === "number" && typeof approveAbove === "number" && rejectBelow >= approveAbove
      ? "отказ должен быть ниже автоодобрения"
      : null;
  const hasInputErrors = !!parseError || !!orderError || Object.values(fieldErrors).some(Boolean);

  function updateQuickField(field: QuickField, raw: string) {
    if (!parsed || raw === "") return;
    // Запятая вместо точки (ваше замечание): <input type="number"> в русской
    // локали браузера показывает и принимает дробные через запятую, но
    // Number("0,75") — NaN, и правка тихо терялась. Поля теперь type="text" —
    // сами разбираем ввод, не зависим от того, как конкретный браузер/локаль
    // решит обработать запятую в number-поле.
    const normalized = raw.replace(",", ".");
    const typed = Number(normalized);
    if (Number.isNaN(typed)) return;
    const value = field.percent ? typed / 100 : typed;
    const next = structuredClone(parsed);
    field.set(next, value);
    setYamlText(yaml.dump(next, { sortKeys: false }));
  }

  const simulateMutation = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/rulesets/simulate", { body: { yaml: yamlText } });
      if (error) throw new Error(typeof (error as any).detail === "string" ? (error as any).detail : "не удалось посчитать симуляцию");
      return data as unknown as SimulateResponse;
    },
    onSuccess: (data) => {
      setSimulateError(null);
      setSaveSuccess(null);
      setSimulation(data);
    },
    onError: (error: Error) => setSimulateError(error.message),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!simulation) throw new Error("сначала прогоните симуляцию");
      const { data, error } = await api.POST("/api/rulesets/save", {
        body: { yaml: yamlText, simulation_id: simulation.simulation_id, comment: publishComment.trim() },
      });
      if (error) throw new Error(typeof (error as any).detail === "string" ? (error as any).detail : "не удалось сохранить");
      return data as unknown as { version: number };
    },
    onSuccess: (data) => {
      setSaveError(null);
      setSaveSuccess(`Сохранено как версия ${data.version}.`);
      setSimulation(null);
      setPublishComment("");
      void queryClient.invalidateQueries({ queryKey: ["ruleset-versions"] });
      void queryClient.invalidateQueries({ queryKey: ["ruleset-active"] });
    },
    onError: (error: Error) => setSaveError(error.message),
  });

  const rollbackMutation = useMutation({
    mutationFn: async (input: { version: number; reason: string }) => {
      const { data, error } = await api.POST("/api/rulesets/rollback", {
        body: { version: input.version, reason: input.reason },
      });
      if (error) throw new Error(typeof (error as any).detail === "string" ? (error as any).detail : "не удалось откатить");
      return data as unknown as { version: number };
    },
    onSuccess: (data) => {
      setRollbackError(null);
      setRollbackTarget(null);
      setRollbackReason("");
      setSaveSuccess(`Откат сохранён как версия ${data.version}.`);
      void queryClient.invalidateQueries({ queryKey: ["ruleset-versions"] });
      void queryClient.invalidateQueries({ queryKey: ["ruleset-active"] });
    },
    onError: (error: Error) => setRollbackError(error.message),
  });

  const acceptFileMutation = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/rulesets/accept-file");
      if (error) throw new Error(typeof (error as any).detail === "string" ? (error as any).detail : "не удалось принять файл");
      return data as unknown as { version: number; created: boolean };
    },
    onSuccess: (data) => {
      setSaveSuccess(
        data.created ? `Версия из файла принята как версия ${data.version}.` : "Активная версия уже совпадает с файлом.",
      );
      void queryClient.invalidateQueries({ queryKey: ["ruleset-versions"] });
      void queryClient.invalidateQueries({ queryKey: ["ruleset-active"] });
    },
  });

  if (activeQuery.isLoading) return <p className="muted">Загрузка…</p>;
  if (activeQuery.isError || !activeQuery.data) {
    const forbidden = activeQuery.error instanceof Error && activeQuery.error.message === "FORBIDDEN";
    return (
      <p role="alert" className="alert">
        {forbidden ? "Недостаточно прав." : "Не удалось загрузить активную версию правил."}
      </p>
    );
  }

  const fileConflict = versionsQuery.data?.file_conflict;

  return (
    <section>
      <h1>Скоринг</h1>
      <p className="muted">
        Активная версия: {activeQuery.data.version}
        {activeQuery.data.comment && ` — ${activeQuery.data.comment}`}
      </p>

      {fileConflict?.conflict && (
        <div className="alert" role="alert">
          <p>
            Файл конфига на диске разошёлся с активной версией, сохранённой через админку. Следующий деплой не
            перезапишет её автоматически.
          </p>
          <button className="btn btn-small" disabled={acceptFileMutation.isPending} onClick={() => acceptFileMutation.mutate()}>
            Принять версию из файла
          </button>
        </div>
      )}
      {fileConflict && !fileConflict.file_valid && (
        <p role="alert" className="alert">Файл конфига на диске не проходит схему: {fileConflict.error}</p>
      )}

      <div className="grid-2 stack">
        <div>
          {(["thresholds", "limits", "pti"] as const).map((section) => (
            <div key={section}>
              <h2>{SECTION_TITLES[section]}</h2>
              {QUICK_FIELDS.filter((f) => f.section === section).map((f) => {
                const raw = parsed ? f.get(parsed) : null;
                const displayed = raw == null ? "" : f.percent ? Math.round(Number(raw) * 1000) / 10 : (raw as number);
                const err = fieldErrors[f.key];
                return (
                  <label className="field" key={f.key}>
                    {f.label}
                    {f.percent ? " (%)" : ""}
                    <input
                      type="text"
                      inputMode="decimal"
                      value={String(displayed)}
                      aria-invalid={!!err}
                      onChange={(e) => updateQuickField(f, e.target.value)}
                    />
                    <span className="field-hint">{f.help}</span>
                    {err && <span role="alert" className="field-error">{err}</span>}
                  </label>
                );
              })}
              {section === "thresholds" && orderError && (
                <p role="alert" className="alert">{orderError}</p>
              )}
            </div>
          ))}

          <details className="disclosure">
            <summary>Экспертный режим — YAML напрямую</summary>
            <textarea
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              rows={24}
              className="code-input stack"
            />
            {parseError && <p role="alert" className="alert">YAML не читается: {parseError}</p>}
          </details>

          <div className="stack">
            <button
              className="btn btn-primary"
              disabled={simulateMutation.isPending || hasInputErrors}
              onClick={() => simulateMutation.mutate()}
            >
              Проверить на истории
            </button>
          </div>

          <label className="field">
            Комментарий к версии (обязательно)
            <input
              type="text"
              value={publishComment}
              onChange={(e) => setPublishComment(e.target.value)}
              placeholder="что меняете и зачем"
            />
          </label>
          <div className="row stack">
            <button
              className="btn btn-primary"
              disabled={!simulation || !publishComment.trim() || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              Опубликовать
            </button>
          </div>
          {simulateError && <p role="alert" className="alert">{simulateError}</p>}
          {saveError && <p role="alert" className="alert">{saveError}</p>}
          {saveSuccess && <p className="badge badge-success">{saveSuccess}</p>}
        </div>

        <div>
          <h2>Результат симуляции</h2>
          {!simulation && <p className="muted">Прогоните симуляцию, чтобы увидеть результат.</p>}
          {simulation && (
            <>
              {(() => {
                const s = simulation.summary;
                const notCompared = s.unknown_after_stage_a_pass_total + s.insufficient_data_count;
                return (
                  <>
                    <p className="muted">
                      В выборке {s.sample_size_actual} {countWord(s.sample_size_actual)} из{" "}
                      {s.sample_size_requested} запрошенных: для {s.known_denominator} {ofWord(s.known_denominator)}{" "}
                      обе версии правил дают известный исход, они сравнивались; для {notCompared}{" "}
                      {ofWord(notCompared)} сравнение невозможно (подробности ниже). Симуляция действует до{" "}
                      {formatDateTime(simulation.expires_at)}.
                    </p>
                    <p className="stack">
                      Новый конфиг одобрил бы{" "}
                      {s.swap_in.length > 0 ? `ещё ${s.swap_in.length} ${countWord(s.swap_in.length)}` : "никого дополнительно"}
                      {s.swap_out.length > 0 && `, и не одобрил бы ${s.swap_out.length} ${countWord(s.swap_out.length)}`}.
                    </p>
                    <table className="table table--kv">
                      <tbody>
                        <tr>
                          <td>Решение изменилось бы</td>
                          <td>
                            {s.changed_count} {countWord(s.changed_count)} из {s.known_denominator} сравненных{" "}
                            {ofWord(s.known_denominator)}
                          </td>
                        </tr>
                        <tr>
                          <td>Доля одобрений сейчас</td>
                          <td>{formatRate(s.approval_rate_active)}</td>
                        </tr>
                        <tr>
                          <td>Доля одобрений по новому конфигу</td>
                          <td>{formatRate(s.approval_rate_candidate)}</td>
                        </tr>
                        {!s.defaultness_by_known_outcome.available && (
                          <tr>
                            <td>Дефолтность по известным исходам</td>
                            <td className="muted">{s.defaultness_by_known_outcome.note}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>

                    {s.defaultness_by_known_outcome.available && (
                      <div className="stack">
                        <h3>Дефолтность по известным исходам (зрелые договоры)</h3>
                        <table className="table">
                          <thead>
                            <tr>
                              <th>Исход</th>
                              <th>Сейчас</th>
                              <th>По новому конфигу</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Array.from(
                              new Set([
                                ...Object.keys(s.defaultness_by_known_outcome.by_active_outcome),
                                ...Object.keys(s.defaultness_by_known_outcome.by_candidate_outcome),
                              ]),
                            ).map((outcome) => {
                              const a = s.defaultness_by_known_outcome.available
                                ? s.defaultness_by_known_outcome.by_active_outcome[outcome]
                                : undefined;
                              const c = s.defaultness_by_known_outcome.available
                                ? s.defaultness_by_known_outcome.by_candidate_outcome[outcome]
                                : undefined;
                              return (
                                <tr key={outcome}>
                                  <td>{outcomeLabel(outcome)}</td>
                                  <td>{a ? `${formatRate(a.bad_rate)} (${a.bad} из ${a.known})` : "—"}</td>
                                  <td>{c ? `${formatRate(c.bad_rate)} (${c.bad} из ${c.known})` : "—"}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}

                    <ApplicationsTable title="Swap-in — теперь одобрили бы" rows={s.swap_in} emptyText="Нет заявок." />
                    <ApplicationsTable
                      title="Swap-out — теперь не одобрили бы"
                      rows={s.swap_out}
                      emptyText="Нет заявок."
                    />

                    <div className="stack">
                      <h3>Прошла бы стадию А, дальше решение неизвестно</h3>
                      <p className="muted">
                        Для {s.unknown_after_stage_a_pass_total} {ofWord(s.unknown_after_stage_a_pass_total)} снимка
                        стадии Б нет (в реальности отклонены на стадии А) — дальнейший исход по новому конфигу
                        непредсказуем, не считается ни «изменилось», ни «не изменилось».
                      </p>
                      {s.unknown_after_stage_a_pass.length > 0 && (
                        <ApplicationsTable title="" rows={s.unknown_after_stage_a_pass} emptyText="" />
                      )}
                    </div>

                    {s.not_computable.length > 0 && (
                      <div className="stack">
                        <h3>Не вычислимо по старым снимкам</h3>
                        <p className="hint">
                          В старых снимках признаков нет нужного поля (например, правило появилось позже) — посчитать
                          правило по ним нельзя. Это не значит, что оно бы не сработало.
                        </p>
                        <ul>
                          {s.not_computable.map((r) => (
                            <li key={r.rule_id}>
                              Правило «{r.rule_id}» — для {r.count} {ofWord(r.count)} нет нужного признака в
                              сохранённых снимках.
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <p className="muted stack">
                      Для {s.insufficient_data_count} {ofWord(s.insufficient_data_count)} обе версии правил ещё не
                      дошли до стадии Б (заявка пока ждёт звонка оператора) — рано сравнивать.
                    </p>
                  </>
                );
              })()}
            </>
          )}
        </div>
      </div>

      <h2>История версий</h2>
      {versionsQuery.isLoading && <p className="muted">Загрузка…</p>}
      {versionsQuery.data && (
        <table className="table">
          <thead>
            <tr>
              <th>Версия</th>
              <th>Комментарий</th>
              <th>Вступила в силу</th>
              <th>Что изменилось</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {versionsQuery.data.versions.map((v) => (
              <tr key={v.id}>
                <td>
                  {v.version} {v.is_active && <span className="badge badge-success">активна</span>}
                </td>
                <td>{v.comment ?? "—"}</td>
                <td>{formatDateTime(v.effective_from)}</td>
                <td className="muted">{v.changes.length > 0 ? v.changes.join("; ") : "—"}</td>
                <td>
                  {!v.is_active && (
                    <button className="btn btn-small" onClick={() => setRollbackTarget(v.version)}>
                      Откатить
                    </button>
                  )}
                </td>
                <td></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rollbackTarget != null && (
        <div className="card">
          <p>Откат к версии {rollbackTarget}. Причина обязательна.</p>
          <label className="field">
            Причина
            <input value={rollbackReason} onChange={(e) => setRollbackReason(e.target.value)} />
          </label>
          {rollbackError && <p role="alert" className="alert">{rollbackError}</p>}
          <div className="row stack">
            <button
              className="btn btn-primary btn-small"
              disabled={rollbackMutation.isPending || !rollbackReason.trim()}
              onClick={() => rollbackMutation.mutate({ version: rollbackTarget, reason: rollbackReason })}
            >
              Подтвердить откат
            </button>
            <button className="btn btn-small" onClick={() => setRollbackTarget(null)}>
              Отмена
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
