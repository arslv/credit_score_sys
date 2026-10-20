import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import * as yaml from "js-yaml";
import { api } from "../api/client";
import { ErrorState, LoadingState, PageHeader } from "../components/ui";
import { RulesetReference } from "../components/RulesetReference";
import { formatDateTime } from "../lib/format";
import { locateInYaml } from "../lib/yamlLocate";
import { reasonCodeLabel } from "../lib/labels";

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
    help: "С этого балла — одобрение (если нет стоп-факторов и ФССП пройдена). Для особых случаев — свои пороги в матрице.",
    min: 0, max: 999,
    get: (c) => c?.score_thresholds?.auto_approve_above, set: (c, v) => { c.score_thresholds.auto_approve_above = v; },
  },
  {
    key: "auto_reject_below", label: "Автоотказ ниже", section: "thresholds", unit: "балл",
    help: "Ниже — автоотказ. Между двумя порогами — серая зона, решает оператор.",
    min: 0, max: 999,
    get: (c) => c?.score_thresholds?.auto_reject_below, set: (c, v) => { c.score_thresholds.auto_reject_below = v; },
  },
  {
    key: "amount_min", label: "Мин. сумма", section: "limits", unit: "₽",
    help: "Сумма рассрочки: цена минус первый взнос.",
    min: 0, max: 100_000_000,
    get: (c) => c?.limits?.amount_min, set: (c, v) => { c.limits.amount_min = v; },
  },
  {
    key: "amount_max", label: "Макс. сумма", section: "limits", unit: "₽",
    help: "Больше — отказ, если не подходит другой тариф.",
    min: 0, max: 100_000_000,
    get: (c) => c?.limits?.amount_max, set: (c, v) => { c.limits.amount_max = v; },
  },
  {
    key: "term_min", label: "Мин. срок, мес.", section: "limits", unit: "мес.",
    help: "Короче — отказ.",
    min: 1, max: 120,
    get: (c) => c?.limits?.term_min, set: (c, v) => { c.limits.term_min = v; },
  },
  {
    key: "term_max", label: "Макс. срок, мес.", section: "limits", unit: "мес.",
    help: "Дольше — отказ.",
    min: 1, max: 120,
    get: (c) => c?.limits?.term_max, set: (c, v) => { c.limits.term_max = v; },
  },
  {
    key: "pti_max", label: "ПДН (заявленный до БКИ и по КИ): отказ выше", percent: true, section: "pti", unit: "%",
    help: "Доля дохода на все долги вместе с нашей рассрочкой. Один порог и для слов клиента, и для КИ.",
    min: 0, max: 100,
    get: (c) => c?.limits?.pti_max, set: (c, v) => { c.limits.pti_max = v; },
  },
  {
    key: "pti_high", label: "ПДН по КИ: ручная проверка выше", percent: true, section: "pti", unit: "%",
    help: "Выше — серая зона (но не выше порога отказа).",
    min: 0, max: 100,
    get: (c) => findRule(c, "pti_high")?.value,
    set: (c, v) => { const r = findRule(c, "pti_high"); if (r) r.value = v; },
  },
];

// История версий: сразу видно последние, остальные — по кнопке.
const VERSIONS_SHOWN = 6;

const SECTION_TITLES: Record<QuickField["section"], string> = {
  thresholds: "Пороги решения (балл БКИ)",
  limits: "Лимиты сделки",
  pti: "Долговая нагрузка (ПДН)",
};

function OutcomeWithReason({ outcome, reason }: { outcome: string; reason: string | null }) {
  return (
    <>
      {outcomeLabel(outcome)}
      {reason && <span className="cell-secondary">{reasonCodeLabel(reason)}</span>}
    </>
  );
}

function ApplicationRowView({ row }: { row: ApplicationRow }) {
  return (
    <tr>
      <td className="cell-top">
        <Link to={`/applications/${row.application_id}`}>{row.external_id}</Link>
        {row.client_name && <span className="cell-secondary">{row.client_name}</span>}
      </td>
      <td className="cell-top">
        <OutcomeWithReason outcome={row.active_outcome} reason={row.active_reason_code} />
      </td>
      <td className="cell-top sim-arrow">→</td>
      <td className="cell-top">
        <OutcomeWithReason outcome={row.candidate_outcome} reason={row.candidate_reason_code} />
      </td>
    </tr>
  );
}

function ApplicationsTable({ title, rows }: { title: string; rows: ApplicationRow[] }) {
  return (
    <div className="sim-block">
      {title && (
        <h3>
          {title} <span className="muted">· {rows.length}</span>
        </h3>
      )}
      <table className="table table--compact">
        <thead>
          <tr>
            <th>Заявка</th>
            <th>Сейчас</th>
            <th />
            <th>По новым правилам</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <ApplicationRowView key={r.application_id} row={r} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// T23 (правка по скриншоту): сверху — главный вывод крупно и три показателя
// карточками; списки заявок — только непустые; всё, что объясняет, ПОЧЕМУ
// часть заявок не сравнивалась, — в свёрнутых «Подробностях». Данные те же.
function SimulationResult({ simulation }: { simulation: SimulateResponse }) {
  const s = simulation.summary;
  const notCompared = s.unknown_after_stage_a_pass_total + s.insufficient_data_count;
  const rateDelta =
    s.approval_rate_active != null && s.approval_rate_candidate != null
      ? s.approval_rate_candidate - s.approval_rate_active
      : null;
  const tone = s.changed_count === 0 ? "same" : s.swap_out.length > s.swap_in.length ? "down" : "up";
  const defaults = s.defaultness_by_known_outcome;
  const approvedBad = defaults.available
    ? { active: defaults.by_active_outcome.approved, candidate: defaults.by_candidate_outcome.approved }
    : null;

  return (
    <div className="sim">
      <div className={`sim-headline sim-headline--${tone}`}>
        <div className="sim-headline__value">
          {s.changed_count === 0 ? "Без изменений" : `${s.changed_count} из ${s.known_denominator}`}
        </div>
        <div className="sim-headline__label">
          {s.changed_count === 0
            ? `ни одно из ${s.known_denominator} сравненных решений не поменяется`
            : `${countWord(s.changed_count)} получили бы другое решение по новым правилам`}
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi__label">Доля одобрений</div>
          <div className="kpi__value">
            {formatRate(s.approval_rate_active)} <span className="kpi__arrow">→</span>{" "}
            {formatRate(s.approval_rate_candidate)}
          </div>
          <div className={`kpi__delta${rateDelta && rateDelta < 0 ? " kpi__delta--down" : rateDelta ? " kpi__delta--up" : ""}`}>
            {rateDelta == null || Math.abs(rateDelta) < 0.0005
              ? "не меняется"
              : `${rateDelta > 0 ? "+" : "−"}${Math.abs(rateDelta * 100).toFixed(1)} п.п.`}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi__label">Станут одобрены</div>
          <div className="kpi__value">{s.swap_in.length}</div>
          <div className="kpi__delta">{s.swap_in.length ? "см. список ниже" : "никто"}</div>
        </div>
        <div className="kpi">
          <div className="kpi__label">Перестанут одобряться</div>
          <div className="kpi__value">{s.swap_out.length}</div>
          <div className="kpi__delta">{s.swap_out.length ? "см. список ниже" : "никто"}</div>
        </div>
        {approvedBad && (approvedBad.active || approvedBad.candidate) && (
          <div className="kpi">
            <div className="kpi__label">Плохой исход у одобренных</div>
            <div className="kpi__value">
              {approvedBad.active ? formatRate(approvedBad.active.bad_rate) : "—"} <span className="kpi__arrow">→</span>{" "}
              {approvedBad.candidate ? formatRate(approvedBad.candidate.bad_rate) : "—"}
            </div>
            <div className="kpi__delta">
              по {approvedBad.active?.known ?? 0} зрелым договорам
            </div>
          </div>
        )}
      </div>

      <p className="hint">
        В выборке {s.sample_size_actual} {countWord(s.sample_size_actual)}, сравнили {s.known_denominator}
        {notCompared > 0 && `, ещё ${notCompared} сравнить нельзя (см. подробности)`}. Результат действует до{" "}
        {formatDateTime(simulation.expires_at)}.
      </p>

      {s.swap_in.length > 0 && <ApplicationsTable title="Станут одобрены" rows={s.swap_in} />}
      {s.swap_out.length > 0 && <ApplicationsTable title="Перестанут одобряться" rows={s.swap_out} />}

      <details className="disclosure">
        <summary>Подробности: что не вошло в сравнение</summary>
        <ul className="sim-notes">
          <li>
            Запрошено {s.sample_size_requested}, в выборке {s.sample_size_actual}{" "}
            {countWord(s.sample_size_actual)}; обе версии дают известный исход у {s.known_denominator}.
          </li>
          {s.insufficient_data_count > 0 && (
            <li>
              {s.insufficient_data_count} {countWord(s.insufficient_data_count)} ещё ждут звонка оператора — до
              стадии Б не дошли, рано сравнивать.
            </li>
          )}
          {s.unknown_after_stage_a_pass_total > 0 && (
            <li>
              {s.unknown_after_stage_a_pass_total} {countWord(s.unknown_after_stage_a_pass_total)} по новым правилам
              прошли бы стадию А, но в реальности отклонены на ней — дальнейшее решение предсказать нельзя.
            </li>
          )}
          {s.not_computable.map((r) => (
            <li key={r.rule_id}>
              Правило «{r.rule_id}» нельзя посчитать для {r.count} {ofWord(r.count)}: в старых снимках нет нужного
              признака (правило появилось позже). Это не значит, что оно бы не сработало.
            </li>
          ))}
          {!defaults.available && <li>Плохие исходы: {defaults.note}</li>}
        </ul>
        {s.unknown_after_stage_a_pass.length > 0 && (
          <ApplicationsTable title="Прошли бы стадию А" rows={s.unknown_after_stage_a_pass} />
        )}
      </details>
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
  const [showAllVersions, setShowAllVersions] = useState(false);
  const yamlRef = useRef<HTMLTextAreaElement>(null);

  // Справочник → место в YAML: выделяем строку и прокручиваем её в верхнюю треть.
  function locateInEditor(path: string): boolean {
    const textarea = yamlRef.current;
    const spot = locateInYaml(yamlText, path);
    if (!textarea || !spot) return false;
    const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
    textarea.focus({ preventScroll: true });
    textarea.setSelectionRange(spot.start, spot.end);
    textarea.scrollTop = Math.max(0, spot.line * lineHeight - textarea.clientHeight / 3);
    return true;
  }

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

  if (activeQuery.isLoading) return <LoadingState />;
  if (activeQuery.isError || !activeQuery.data) {
    const forbidden = activeQuery.error instanceof Error && activeQuery.error.message === "FORBIDDEN";
    return <ErrorState message={forbidden ? "Недостаточно прав." : "Не удалось загрузить активную версию правил."} />;
  }

  const fileConflict = versionsQuery.data?.file_conflict;

  return (
    <section>
      <PageHeader
        title="Скоринг"
        description={
          <>
            Активная версия: {activeQuery.data.version}
            {activeQuery.data.comment && ` — ${activeQuery.data.comment}`}
          </>
        }
      />

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

      {/* T23 (по отзыву): компактно. Слева — типовые пороги строками «название ·
          подсказка · значение»; справа — шаги проверки и публикации с результатом
          симуляции; ниже — экспертный режим: YAML и справочник полей рядом. */}
      <div className="rules-grid">
        <div className="rules-form">
          {(["thresholds", "limits", "pti"] as const).map((section) => (
            <section key={section} className="rules-group">
              <h2 className="rules-group__title">{SECTION_TITLES[section]}</h2>
              {QUICK_FIELDS.filter((f) => f.section === section).map((f) => {
                const raw = parsed ? f.get(parsed) : null;
                const displayed = raw == null ? "" : f.percent ? Math.round(Number(raw) * 1000) / 10 : (raw as number);
                const err = fieldErrors[f.key];
                const inputId = `rule-${f.key}`;
                return (
                  <div className="rule-row" key={f.key}>
                    <div className="rule-row__text">
                      <label htmlFor={inputId} className="rule-row__label">
                        {f.label}
                        {f.percent ? " (%)" : ""}
                      </label>
                      <span className="rule-row__hint">{f.help}</span>
                      {err && <span role="alert" className="field-error">{err}</span>}
                    </div>
                    <div className={err ? "rule-row__input rule-row__input--error" : "rule-row__input"}>
                      <input
                        id={inputId}
                        type="text"
                        inputMode="decimal"
                        value={String(displayed)}
                        aria-invalid={!!err}
                        onChange={(e) => updateQuickField(f, e.target.value)}
                      />
                      <span className="rule-row__unit">{f.percent ? "%" : f.unit}</span>
                    </div>
                  </div>
                );
              })}
              {section === "thresholds" && orderError && (
                <p role="alert" className="alert">{orderError}</p>
              )}
            </section>
          ))}
          <p className="rules-more">
            Остальное — стоп-факторы, матрица особых условий, тарифы, источники — в экспертном режиме ниже.
          </p>
        </div>

        <aside className="rules-publish">
          <div className="rules-step">
            <div className="rules-step__head">
              <span className="rules-step__n">1</span>Проверьте на истории заявок
            </div>
            <button
              className="btn"
              disabled={simulateMutation.isPending || hasInputErrors}
              onClick={() => simulateMutation.mutate()}
            >
              {simulateMutation.isPending ? "Считаем…" : "Проверить на истории"}
            </button>
            {simulateError && <p role="alert" className="alert">{simulateError}</p>}
            {!simulation && (
              <p className="rules-step__hint">
                Измените пороги и нажмите — увидите, сколько решений поменяется. Без проверки опубликовать нельзя.
              </p>
            )}
            {simulation && <SimulationResult simulation={simulation} />}
          </div>
          <div className="rules-step">
            <div className="rules-step__head">
              <span className="rules-step__n">2</span>Опишите, что и зачем меняете
            </div>
            <label className="field">
              Комментарий к версии (обязательно)
              <input
                type="text"
                value={publishComment}
                onChange={(e) => setPublishComment(e.target.value)}
                placeholder="например: порог одобрения 720 → 700 по итогам пилота"
              />
            </label>
          </div>
          <div className="rules-step">
            <div className="rules-step__head">
              <span className="rules-step__n">3</span>Опубликуйте
            </div>
            <button
              className="btn btn-primary"
              disabled={!simulation || !publishComment.trim() || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              Опубликовать
            </button>
            {saveError && <p role="alert" className="alert">{saveError}</p>}
            {saveSuccess && <p className="badge badge-success">{saveSuccess}</p>}
          </div>
        </aside>
      </div>

      <details className="rules-expert">
        <summary>
          Экспертный режим — YAML напрямую
          <span className="muted"> · все правила конфига и справочник полей</span>
        </summary>
        <div className="rules-expert__body">
          <div className="rules-expert__editor">
            <textarea
              ref={yamlRef}
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              spellCheck={false}
              aria-label="YAML конфига правил"
              className="code-input"
            />
            {parseError && <p role="alert" className="alert">YAML не читается: {parseError}</p>}
            <p className="rules-step__hint">
              Правки здесь и в полях выше — одно и то же: поля просто меняют этот текст. Проверка на истории и
              публикация — те же шаги справа вверху.
            </p>
          </div>
          <RulesetReference onLocate={locateInEditor} />
        </div>
      </details>

      <h2 className="rules-history-title">История версий</h2>
      {versionsQuery.isLoading && <LoadingState />}
      {versionsQuery.data && (
        <>
          <table className="table table--compact rules-history">
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
              {(showAllVersions ? versionsQuery.data.versions : versionsQuery.data.versions.slice(0, VERSIONS_SHOWN)).map(
                (v) => (
                  <tr key={v.id} className={v.is_active ? "rules-history__active" : undefined}>
                    <td>{v.version}</td>
                    <td>
                      {v.comment ?? "—"}
                      {v.is_active && <span className="badge badge-success rules-history__badge">активна</span>}
                    </td>
                    <td className="muted">{formatDateTime(v.effective_from)}</td>
                    <td className="muted">{v.changes.length > 0 ? v.changes.join("; ") : "—"}</td>
                    <td className="cell-actions">
                      {!v.is_active && (
                        <button className="btn btn-small" onClick={() => setRollbackTarget(v.version)}>
                          Откатить
                        </button>
                      )}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
          {versionsQuery.data.versions.length > VERSIONS_SHOWN && (
            <button type="button" className="btn-link rules-history__more" onClick={() => setShowAllVersions((v) => !v)}>
              {showAllVersions ? "Свернуть" : `Показать все версии (${versionsQuery.data.versions.length})`}
            </button>
          )}
        </>
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
