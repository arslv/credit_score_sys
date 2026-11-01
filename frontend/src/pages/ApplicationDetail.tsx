import { useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { ClientRatingPanel } from "../components/ClientRating";
import { FedresursPanel } from "../components/FedresursPanel";
import { BkiRequestPanel } from "../components/BkiRequestPanel";
import { FsspPanel } from "../components/FsspPanel";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { VerdictTitle } from "../components/VerdictTitle";
import { ErrorState, LoadingState } from "../components/ui";
import { ageYears, clientRows, dealRows, latestStageBPayload, ocrValue } from "../lib/card";
import { explainDecision } from "../lib/explain";
import { formatAmount, formatDate, formatDateTime } from "../lib/format";
import {
  actorTypeLabel,
  applicationStatusLabel,
  checkResultLabel,
  decisionOutcomeLabel,
  documentKindLabel,
  humanizeFieldPath,
  overrideOutcomeLabel,
  passportCheckLabel,
  passportCheckStatusLabel,
  reasonCodeLabel,
  sourceMeta,
  stageLabel,
  subjectTypeLabel,
} from "../lib/labels";

type Decision = components["schemas"]["DecisionOut"];
type CheckRow = components["schemas"]["CheckOut"];
type BureauCheck = components["schemas"]["BureauCheckOut"];

// Проверка из подробного отчёта бюро — рядом с нашей, с этой пометкой; наша
// проверка — без пометки (решение владельца: обе видны, в решение входит наша).
function BkiTag() {
  return (
    <span className="bki-tag" title="Проверка из отчёта бюро кредитных историй, не наша">
      БКИ
    </span>
  );
}

const capitalize = (text: string) => (text ? text[0]!.toUpperCase() + text.slice(1) : text);

// Строки «по данным БКИ» под нашей ручной проверкой того же источника.
function BureauLines({ checks }: { checks: BureauCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="bureau-lines">
      {checks.map((b) => (
        <li key={b.id} className={`manual-check__status bureau-line bureau-line--${checkTone(b.status)}`}>
          <b>{capitalize(subjectTypeLabel(b.subject_type))}:</b> {checkResultLabel(b.source, b.status)} <BkiTag />
          <span className="muted"> · {formatDateTime(b.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}
type ManualOverride = components["schemas"]["ManualOverrideOut"];
type EventTone = "ok" | "bad" | "warn" | "info" | "wait";
type JourneyState = "done" | "bad" | "warn" | "current" | "pending" | "skipped";
const JOURNEY_ICON: Partial<Record<JourneyState, string>> = { done: "✓", bad: "✕", warn: "!", skipped: "–" };

// Статусы, которые просто повторяют последнее решение системы.
const STATUS_MIRRORS_DECISION = new Set(["approved", "gray_zone", "rejected_a", "rejected_b", "to_operator"]);

// Числа в карточке — без копеек (780, а не 780.00): баллы и пороги целые.
function wholeNumber(value: number | string): string {
  return String(Math.round(Number(value)));
}

function verdictTone(outcome: string | undefined): string {
  if (outcome === "approve") return "pf-verdict--ok";
  if (outcome === "reject") return "pf-verdict--bad";
  if (outcome === "manual_review") return "pf-verdict--warn";
  return "pf-verdict--wait";
}

function decisionEventTone(d: Decision): EventTone {
  if (d.outcome === "approve") return "ok";
  if (d.outcome === "reject") return "bad";
  return d.stage === "B" ? "warn" : "info";
}

const EVENT_ICON: Record<EventTone, string> = { ok: "✓", bad: "✕", warn: "!", info: "→", wait: "…" };

// Последняя проверка по каждой паре «источник + субъект» (порядок — как в исходном списке).
function latestPerSource(checks: CheckRow[]): CheckRow[] {
  const latest = new Map<string, CheckRow>();
  for (const c of checks) {
    const key = `${c.source}|${c.subject_type}|${c.subject_id ?? ""}`;
    const prev = latest.get(key);
    if (!prev || c.started_at > prev.started_at) latest.set(key, c);
  }
  return checks.filter((c) => latest.get(`${c.source}|${c.subject_type}|${c.subject_id ?? ""}`) === c);
}

// Инициалы для аватара: первые буквы фамилии и имени.
function initials(name: string | null): string {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join("");
}

// T23: оператор в CRM сменил статус у себя после решения системы — что
// решила система, что поставил оператор и почему, обычными словами.
function OperatorOverride({ override: o }: { override: ManualOverride }) {
  const tone = o.reason_code === "PARTNER_OPERATOR_OVERRIDE_APPROVE" ? "approve" : "reject";
  return (
    <div className={`override override--${tone}`}>
      <div className="override__head">
        <span className="override__title">{reasonCodeLabel(o.reason_code)}</span>
      </div>
      <dl className="override__rows">
        <dt>Решение системы</dt>
        <dd>{overrideOutcomeLabel(o.decision_stage, o.from_outcome)}</dd>
        <dt>Итог оператора</dt>
        <dd>
          <b>{overrideOutcomeLabel(o.decision_stage, o.to_outcome)}</b>
        </dd>
        <dt>Причина</dt>
        <dd>{o.comment ?? "оператор не указал"}</dd>
      </dl>
    </div>
  );
}

// Проверки стадии — точками в строке события, без пояснений: что не в
// порядке, видно по цвету, подробности — по «подробнее» у события.
function CheckChips({
  checks,
  passport,
  bureau = [],
}: {
  checks: CheckRow[];
  passport: PassportCheck[];
  bureau?: BureauCheck[];
}) {
  if (checks.length === 0 && passport.length === 0 && bureau.length === 0) return null;
  const passportTone = passport.some((i) => i.status === "fail")
    ? "bad"
    : passport.some((i) => i.status !== "ok")
      ? "warn"
      : "ok";
  const passportOk = passport.filter((i) => i.status === "ok").length;
  return (
    <ul className="ev__chips">
      {passport.length > 0 && (
        <li className={`ev__chip ev__chip--${passportTone}`}>
          Паспорт {passportOk}/{passport.length}
        </li>
      )}
      {checks.map((c) => (
        <li
          key={c.id}
          className={`ev__chip ev__chip--${checkTone(c.status)}`}
          title={checkResultLabel(c.source, c.status)}
        >
          {sourceMeta(c.source).label}
          {c.subject_type === "guarantor" && " (поручитель)"}
        </li>
      ))}
      {bureau.map((b) => (
        <li key={b.id} className={`ev__chip ev__chip--${checkTone(b.status)}`} title={checkResultLabel(b.source, b.status)}>
          {sourceMeta(b.source).label} <BkiTag />
        </li>
      ))}
    </ul>
  );
}

type FeedEvent = {
  key: string;
  at: string | null;
  tone: EventTone;
  title: string;
  chips?: React.ReactNode;
  details?: React.ReactNode;
};

// Событие хода заявки — одна строка: значок, что произошло, проверки
// точками, время. Причины и проверки по пунктам — по «подробнее» (в DOM
// остаются, только скрыты).
function FeedRow({ event: e }: { event: FeedEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`ev ev--${e.tone}`}>
      <span className="ev__icon" aria-hidden="true">
        {EVENT_ICON[e.tone]}
      </span>
      <div className="ev__line">
        <h3 className="ev__title">{e.title}</h3>
        {e.chips}
        {e.details && (
          <button type="button" className="btn-link ev__more" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "скрыть" : "подробнее"}
          </button>
        )}
        {e.at && <span className="ev__time">{formatDateTime(e.at)}</span>}
      </div>
      {e.details && (
        <div className="ev__details" hidden={!open}>
          {e.details}
        </div>
      )}
    </li>
  );
}

export function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { employee } = useAuth();
  const queryClient = useQueryClient();
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);
  const [decisionStub, setDecisionStub] = useState<"accept" | "reject" | null>(null);

  const query = useQuery({
    queryKey: ["application", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications/{application_id}", {
        params: { path: { application_id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
    // Отчёт БКИ заказан — решение придёт фоновой задачей, обновляем карточку.
    refetchInterval: (q) => (q.state.data?.status === "awaiting_bki" && q.state.data?.bki_order?.pending ? 5000 : false),
  });

  const retryMutation = useMutation({
    mutationFn: async (checkId: string) => {
      const { data, error } = await api.POST(
        "/api/applications/{application_id}/checks/{check_id}/retry",
        { params: { path: { application_id: id!, check_id: checkId } } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["application", id] });
    },
  });

  if (query.isLoading) return <LoadingState />;
  if (query.isError || !query.data) {
    return <ErrorState message="Не удалось загрузить заявку." onRetry={() => void query.refetch()} />;
  }

  const app = query.data;
  // T23: ролей три — повтор проверки у admin, ФССП у admin и operator.
  const canRetry = employee?.role === "admin";
  const canOperateFssp = employee?.role === "operator" || employee?.role === "admin";
  const retry = (checkId: string) => retryMutation.mutate(checkId);

  const findLatestCheck = (
    source: "fssp" | "fedresurs" | "fns_inn", subjectType: "applicant" | "guarantor", subjectId: string | null,
  ) => {
    const matches = app.checks.filter(
      (c) => c.source === source && c.subject_type === subjectType && (c.subject_id ?? null) === subjectId,
    );
    if (matches.length === 0) return null;
    return matches.reduce((latest, c) => (c.started_at > latest.started_at ? c : latest));
  };

  const latestDecision = app.decisions.length > 0 ? app.decisions[app.decisions.length - 1]! : null;
  const stageB = latestStageBPayload(app.external_snapshots);
  const explanation = latestDecision ? explainDecision(latestDecision) : null;

  const decisionsA = app.decisions.filter((d) => d.stage === "A");
  const decisionsB = app.decisions.filter((d) => d.stage === "B");
  const latestA = decisionsA.length > 0 ? decisionsA[decisionsA.length - 1]! : null;
  const latestB = decisionsB.length > 0 ? decisionsB[decisionsB.length - 1]! : null;
  const rejectedAtA = latestA?.outcome === "reject" && !latestB;
  const reviewSnapshots = app.external_snapshots.filter((s) => s.stage === "B");
  const reviewedAt = reviewSnapshots.length > 0 ? reviewSnapshots[reviewSnapshots.length - 1]!.received_at : null;

  const visibleChecks = app.checks.filter((c) => c.source !== "mvd");
  const bureauChecks = app.bureau_checks ?? [];
  const bureauLatest = bureauChecks.filter(
    (b, i) => !bureauChecks.slice(i + 1).some((n) => n.source === b.source && n.subject_type === b.subject_type),
  );
  const bureauFor = (source: string) => bureauLatest.filter((b) => b.source === source);
  // Поручитель после повторного «Рассмотреть» записывается заново (новый id):
  // в ленте и на шкале — проверки только текущего; вся история — в таблице.
  const currentChecks = visibleChecks.filter(
    (c) => c.subject_type !== "guarantor" || (c.subject_id ?? null) === (app.guarantor_id ?? null),
  );
  // Ручные проверки (ФССП, Федресурс) и поручитель — после звонка, к стадии Б.
  const isManual = (c: CheckRow) => c.source === "fssp" || c.source === "fedresurs";
  // В ленте — только последняя попытка по каждому источнику и субъекту:
  // повторные проверки (ФССП с новой капчей, «Повторить») пишутся новыми
  // строками, вся история — в «Все проверки одной таблицей».
  const latestChecks = latestPerSource(currentChecks);
  const checksA = latestChecks.filter((c) => c.subject_type !== "guarantor" && !isManual(c));
  const checksB = latestChecks.filter((c) => c.subject_type === "guarantor" || isManual(c));

  // T23 (по отзыву): отдельного списка дел нет. Что требует действия здесь —
  // в закреплённом блоке «Нужно ваше действие» с самими проверками; что
  // делается не здесь (серая зона в CRM, проверка по перечням) — строкой под
  // решением.
  const applicantFssp = findLatestCheck("fssp", "applicant", null);
  const applicantFedresurs = findLatestCheck("fedresurs", "applicant", null);
  const manualSubjects: { type: "applicant" | "guarantor"; id: string | null; label: string }[] = [
    { type: "applicant", id: null, label: "заявителя" },
    ...(app.has_guarantor && app.guarantor_id
      ? [{ type: "guarantor" as const, id: app.guarantor_id, label: "поручителя" }]
      : []),
  ];
  // ФССП пройден — есть результат (найдено / не найдено); недоступность сайта
  // или ошибка — не пройден. Банкротство — есть отметка сотрудника.
  const fsspPassed = (c: CheckRow | null) => c?.status === "ok" || c?.status === "hit";
  const manualState = manualSubjects.map((s) => {
    const fssp = findLatestCheck("fssp", s.type, s.id);
    const fed = findLatestCheck("fedresurs", s.type, s.id);
    return { ...s, fssp, fed, fsspDone: fsspPassed(fssp), fedDone: Boolean(fed) };
  });
  const fsspLeft = manualState.filter((s) => !s.fsspDone).map((s) => s.label);
  const fedLeft = manualState.filter((s) => !s.fedDone).map((s) => s.label);
  const grayStageB = latestDecision?.outcome === "manual_review" && latestDecision.stage === "B";
  // Серая зона «ФССП не пройдена» — действие нужно, пока ФССП у кого-то не
  // пройдена; прошли — решение не пересчитывается само (новое — после
  // повторного «Рассмотреть» в CRM), «требуется действие» снимаем.
  const fsspGray = grayStageB && latestDecision?.reason_code === "FSSP_NOT_CHECKED";
  const fsspNeedsAction = (fsspGray && fsspLeft.length > 0) || app.fssp_post_decision_alert_subjects.length > 0;
  const fsspDoneAfterGray = fsspGray && fsspLeft.length === 0;
  const grayInCrm = grayStageB && !fsspGray;
  // Банкротство проверяется вручную — пока по заявителю нет отметки и заявка
  // ещё в работе (после отказа или истечения — незачем).
  const fedresursNeedsAction =
    fedLeft.length > 0 && app.decisions.length > 0 && !["rejected_a", "rejected_b", "expired"].includes(app.status);
  // Что именно осталось — поимённо: «ФССП поручителя, банкротство заявителя и поручителя».
  const joinRu = (items: string[]) =>
    items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} и ${items[items.length - 1]}`;
  const lateFssp = manualState
    .filter((m) => app.fssp_post_decision_alert_subjects.includes(m.type))
    .map((m) => m.label);
  const leftText = [
    fsspNeedsAction && fsspLeft.length > 0 && `ФССП ${joinRu(fsspLeft)}`,
    lateFssp.length > 0 && `пересмотреть решение: у ${joinRu(lateFssp)} ФССП нашёл долг после решения`,
    fedresursNeedsAction && `банкротство ${joinRu(fedLeft)}`,
  ]
    .filter(Boolean)
    .join("; ");
  // Стадия Б ждёт, пока сотрудник закажет отчёт БКИ (запрос платный).
  const bkiWaiting = app.status === "awaiting_bki";
  // Отчёт БКИ не получен (бюро не подключено, недоступно…) — серая зона, но
  // запрос можно повторить теми же кнопками.
  const bkiRetry = app.bki_request_state === "retry";
  const bkiNeedsAction = bkiWaiting && !app.bki_order?.pending;
  const actionCount = Number(fsspNeedsAction) + Number(fedresursNeedsAction) + Number(bkiNeedsAction);
  // Ручные проверки (ФССП, банкротство) — одна карточка на постоянном месте:
  // отметили одну — вторая не перескакивает.
  const manualActionCount = Number(fsspNeedsAction) + Number(fedresursNeedsAction);
  // После отказа или истечения ручные проверки уже ничего не меняют — блок
  // скрыт (результаты, если были, остаются в «Что проверили»). Проверки, что
  // ждут действия, показаны выше в «Нужно ваше действие».
  const manualRelevant = !["rejected_a", "rejected_b", "expired"].includes(app.status);
  const showManual = manualRelevant || fsspNeedsAction || fedresursNeedsAction;

  const clientBlock = clientRows({
    clientName: app.client_name,
    phone: (app.profile?.phone as string | undefined) ?? null,
    ocrFields: app.ocr_result?.fields ?? null,
    stageB,
    now: new Date(),
  });
  const dealBlock = dealRows({
    goodsName: app.goods_name,
    goodsPrice: app.goods_price,
    goodsCategory: app.goods_category,
    downPayment: app.down_payment,
    termMonths: app.term_months,
    tariff: app.tariff,
    approvedAmount: latestDecision?.approved_amount ?? null,
    approvedTerm: latestDecision?.approved_term ?? null,
    approvedTariff: latestDecision?.offered_tariff ?? null,
    approvedDownPay: latestDecision?.required_down_pay ?? null,
    installmentPayment: latestDecision?.installment_payment ?? null,
    installmentSource: latestDecision?.installment_payment_source ?? null,
  });

  // Поиск на Федресурсе: ИНН (если ФНС его нашла — точное совпадение) или ФИО;
  // дата рождения — чтобы сверить найденного однофамильца.
  const innOf = (subjectType: "applicant" | "guarantor", subjectId: string | null) => {
    const inn = findLatestCheck("fns_inn", subjectType, subjectId)?.parsed?.inn;
    return typeof inn === "string" && inn ? inn : null;
  };
  const birthText = (birth: string | null | undefined) => (birth ? formatDate(birth) : null);
  const guarantorName = app.guarantor_profile
    ? [app.guarantor_profile.last_name, app.guarantor_profile.first_name, app.guarantor_profile.middle_name]
        .filter((part) => typeof part === "string" && part)
        .join(" ")
    : null;

  // Профиль клиента: возраст из паспорта, отметки, анкета без ФИО (оно в заголовке).
  const birthDate = ocrValue(app.ocr_result?.fields ?? null, "birth_date");
  const age = birthDate ? ageYears(birthDate, new Date()) : null;
  const guarantorNeeded = (app.client_rating?.guarantor_reasons.length ?? 0) > 0;
  const profileRows = clientBlock.filter((row) => row.label !== "ФИО");
  const innFound = Boolean(innOf("applicant", null));

  // Ход заявки — от новых к старым, по строке на событие. У последнего
  // решения каждой стадии — проверки точками; причины и проверки по пунктам
  // — по «подробнее».
  const events: FeedEvent[] = [
    {
      key: "submitted",
      at: app.submitted_at,
      tone: "ok" as const,
      title: "Заявка подана",
    },
    ...app.decisions.map((d): FeedEvent => {
      const isLatestOfStage = d === (d.stage === "A" ? latestA : latestB);
      const outcome = decisionOutcomeLabel(d.stage, d.outcome);
      const stageChecks = d.stage === "A" ? checksA : checksB;
      const passport = d.stage === "A" ? app.passport_checks : [];
      const reasons =
        d === latestDecision && explanation ? (
          <DecisionReasons explanation={explanation} />
        ) : (
          d.reason_text && <p className="ev__text">{d.reason_text}</p>
        );
      return {
        key: `decision-${d.id}`,
        at: d.created_at,
        tone: decisionEventTone(d),
        title: `Стадия ${stageLabel(d.stage)}: ${outcome[0]!.toLowerCase()}${outcome.slice(1)}`,
        chips: isLatestOfStage ? (
          <CheckChips checks={stageChecks} passport={passport} bureau={d.stage === "B" ? bureauLatest : []} />
        ) : undefined,
        details: isLatestOfStage ? (
          <>
            {reasons}
            {(stageChecks.length > 0 || passport.length > 0) && (
              <CheckList
                checks={stageChecks}
                canRetry={canRetry}
                retry={retry}
                busy={retryMutation.isPending}
                leading={passport.length > 0 ? <PassportRow items={passport} /> : null}
              />
            )}
          </>
        ) : (
          reasons || undefined
        ),
      };
    }),
    ...(reviewedAt ? [{ key: "call", at: reviewedAt, tone: "ok" as const, title: "Оператор нажал «Рассмотреть» в CRM" }] : []),
    ...app.manual_overrides.map(
      (o): FeedEvent => ({
        key: `override-${o.id}`,
        at: o.created_at,
        tone: o.reason_code === "PARTNER_OPERATOR_OVERRIDE_APPROVE" ? "ok" : "bad",
        title: `Оператор CRM: ${overrideOutcomeLabel(o.decision_stage, o.to_outcome).toLowerCase()}`,
        details: <OperatorOverride override={o} />,
      }),
    ),
  ]
    // От новых к старым; при одинаковом времени — в порядке хода заявки
    // (подача → стадия А → звонок → стадия Б), только перевёрнутом.
    .map((e, order) => ({ e, order }))
    .sort((a, b) => (b.e.at ?? "").localeCompare(a.e.at ?? "") || b.order - a.order)
    .map(({ e }) => e);
  // Что ждём дальше — сверху ленты, без времени.
  if (!latestA && !reviewedAt && !latestB) {
    events.unshift({ key: "next", at: null, tone: "wait", title: "Идут проверки стадии А" });
  } else if (latestA && !rejectedAtA && !reviewedAt && !latestB) {
    events.unshift({ key: "next", at: null, tone: "wait", title: "Ждём «Рассмотреть» от оператора CRM" });
  }

  const ptiPercent = latestB?.pti != null ? Math.round(latestB.pti * 100) : null;
  const ptiMaxPercent = latestB?.pti_max != null ? Math.round(latestB.pti_max * 100) : null;
  const score = latestDecision?.score ?? null;

  const fsspSection = (
    <section className="mc" key="fssp">
      <div className="mc__head">
        <h3 id="fssp" className="mc__title">
          ФССП
        </h3>
        {fsspNeedsAction && <span className="action-tag">требуется действие</span>}
        {fsspNeedsAction && grayStageB && <span className="mc__why">без неё одобрение недоступно</span>}
      </div>
      <FsspPanel
        applicationId={app.id}
        subjectType="applicant"
        subjectLabel="заявитель"
        subjectId={null}
        latestCheck={applicantFssp}
        canOperate={canOperateFssp}
        hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("applicant")}
      />
      {app.has_guarantor && app.guarantor_id && (
        <FsspPanel
          applicationId={app.id}
          subjectType="guarantor"
          subjectLabel="поручитель"
          subjectName={guarantorName || null}
          subjectId={app.guarantor_id}
          latestCheck={findLatestCheck("fssp", "guarantor", app.guarantor_id)}
          canOperate={canOperateFssp}
          hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("guarantor")}
        />
      )}
      <BureauLines checks={bureauFor("fssp")} />
    </section>
  );

  const fedresursSection = (
    <section className="mc" key="fedresurs">
      <div className="mc__head">
        <h3 id="fedresurs" className="mc__title">
          Банкротство (Федресурс)
        </h3>
        {fedresursNeedsAction && <span className="action-tag">требуется действие</span>}
      </div>
      <FedresursPanel
        applicationId={app.id}
        subjectType="applicant"
        subjectLabel="заявитель"
        subjectId={null}
        fullName={app.client_name ?? null}
        birthDate={birthText(birthDate)}
        inn={innOf("applicant", null)}
        latestCheck={applicantFedresurs}
        canOperate={canOperateFssp}
      />
      {app.has_guarantor && app.guarantor_id && (
        <FedresursPanel
          applicationId={app.id}
          subjectType="guarantor"
          subjectLabel="поручитель"
          subjectId={app.guarantor_id}
          fullName={guarantorName || null}
          birthDate={birthText(app.guarantor_profile?.birth_date as string | undefined)}
          inn={innOf("guarantor", app.guarantor_id)}
          latestCheck={findLatestCheck("fedresurs", "guarantor", app.guarantor_id)}
          canOperate={canOperateFssp}
        />
      )}
      <BureauLines checks={bureauFor("fedresurs")} />
    </section>
  );

  // Путь заявки целиком — что пройдено и что осталось до окончательного решения.
  const manualHit = manualState.some((s) => s.fssp?.status === "hit" || s.fed?.status === "hit");
  const lastOverride = app.manual_overrides.length > 0 ? app.manual_overrides[app.manual_overrides.length - 1]! : null;
  const callDone = Boolean(reviewedAt) || Boolean(latestB);
  const manualLeft = [
    fsspLeft.length > 0 && `ФССП ${joinRu(fsspLeft)}`,
    fedLeft.length > 0 && `банкротство ${joinRu(fedLeft)}`,
  ]
    .filter(Boolean)
    .join(", ");
  const journey: { label: string; note: string; state: JourneyState }[] = [
    { label: "Подача", note: formatDateTime(app.submitted_at), state: "done" },
    {
      label: "Стадия А",
      note: latestA ? decisionOutcomeLabel("A", latestA.outcome) : callDone ? "нет записи" : "идёт",
      state: latestA ? (latestA.outcome === "reject" ? "bad" : "done") : callDone ? "skipped" : "current",
    },
    {
      label: "Звонок оператора",
      // Решение стадии Б без снимка «Рассмотреть» — звонок был, времени нет.
      note: reviewedAt ? formatDateTime(reviewedAt) : callDone ? "был" : rejectedAtA ? "не понадобился" : latestA ? "ждём" : "впереди",
      state: callDone ? "done" : rejectedAtA ? "skipped" : latestA ? "current" : "pending",
    },
    {
      label: "Стадия Б",
      note: bkiWaiting
        ? app.bki_order?.pending ? "ждём ответа БКИ" : "ждёт запроса КИ"
        : latestB
        ? decisionOutcomeLabel("B", latestB.outcome)
        : app.status === "stage_b" ? "идёт" : rejectedAtA ? "не проводилась" : "впереди",
      state: bkiWaiting
        ? app.bki_order?.pending ? "current" : "warn"
        : latestB
        ? latestB.outcome === "approve" ? "done" : latestB.outcome === "reject" ? "bad" : "warn"
        : app.status === "stage_b" ? "current" : rejectedAtA ? "skipped" : "pending",
    },
    {
      label: "ФССП и банкротство",
      // После отказа ручные проверки уже ничего не меняют.
      note: manualHit ? "есть находки" : !manualLeft ? "проверены" : latestDecision?.outcome === "reject" ? "не нужны после отказа" : `осталось: ${manualLeft}`,
      state: manualHit ? "bad" : !manualLeft ? "done" : latestDecision?.outcome === "reject" ? "skipped" : actionCount > 0 ? "warn" : "pending",
    },
    {
      label: "Решение в CRM",
      note: lastOverride
        ? `оператор: ${overrideOutcomeLabel(lastOverride.decision_stage, lastOverride.to_outcome).toLowerCase()}`
        : latestDecision?.outcome === "reject" ? "отказ" : latestB?.outcome === "approve" ? "одобрено" : grayStageB ? "за оператором CRM" : "впереди",
      state: lastOverride
        ? "done"
        : latestDecision?.outcome === "reject" ? "bad" : latestB?.outcome === "approve" ? "done" : grayStageB ? "current" : "pending",
    },
  ];
  const ratingTone =
    app.client_rating == null ? "" : app.client_rating.total > 0 ? "ah-metric__value--ok" : app.client_rating.total < 0 ? "ah-metric__value--bad" : "";

  // T23 (по отзыву): сверху — всё, по чему решают «давать или нет»: кто
  // клиент, решение и его причина, кнопки, главные цифры (балл, ПДН, платёж,
  // рейтинг, ИИ) и путь заявки с тем, что осталось. Ниже — что нужно сделать
  // руками, ход заявки и ручные проверки. Анкета, сделка, проверки таблицей,
  // документы и история — свёрнутыми разделами в самом низу.
  return (
    <div className={latestDecision?.outcome === "manual_review" ? "ac2 ac2--gray" : "ac2"}>
      <p className="back-link">
        <Link to="/applications">← Заявки</Link>
      </p>

      <section className={`ah ${verdictTone(latestDecision?.outcome)}`} aria-label="Главное по заявке">
        <div className="ah__top">
          <div className="ah__who">
            <div className="ah__avatar" aria-hidden="true">
              {initials(app.client_name)}
            </div>
            <div className="ah__who-text">
              <h2 className="ah__name">{app.client_name ?? "Клиент скрыт"}</h2>
              <p className="ah__line">
                {[age != null ? `${age} лет` : null, (app.profile?.phone as string | undefined) ?? null]
                  .filter(Boolean)
                  .join(" · ") || "паспортных данных нет"}
              </p>
              <p className="ah__line ah__line--muted">
                Заявка №{app.number} · подана {formatDateTime(app.submitted_at)} · в CRM {app.external_id}
              </p>
              <p className="ah__line">
                {app.goods_name && app.goods_name !== "—" ? app.goods_name : "товар уточняется"}
                {app.term_months > 0 && ` · ${formatAmount(app.requested_amount)} ₽ на ${app.term_months} мес.`}
              </p>
              <div className="ah__tags">
                {innFound && <span className="pill pill-ok">ИНН найден</span>}
                {app.has_guarantor ? (
                  <span className="pill pill-mute">есть поручитель</span>
                ) : guarantorNeeded ? (
                  <span className="pill pill-bad">нужен поручитель</span>
                ) : null}
              </div>
            </div>
          </div>

          <div className="ah__verdict">
            <div className="pf-kicker">
              {latestDecision ? `Решение системы · стадия ${stageLabel(latestDecision.stage)}` : "Решения пока нет"}
            </div>
            <div className="pf-verdict__title">
              <VerdictTitle
                title={latestDecision ? decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome) : "В обработке"}
                help={explanation?.help ?? null}
              />
              {/* Статус дублирует решение («Серая зона» дважды) — показываем,
                  только когда он говорит что-то своё (истекла, идёт стадия Б…). */}
              {!STATUS_MIRRORS_DECISION.has(app.status) && (
                <Badge value={app.status} label={applicationStatusLabel(app.status)} />
              )}
            </div>
            {explanation?.reason && <p className="pf-verdict__reason">{explanation.reason}</p>}
            {grayInCrm && <p className="verdict-action">Решение принимает оператор в CRM.</p>}
            {fsspDoneAfterGray && (
              <p className="verdict-action">
                ФССП уже пройдена — новое решение будет, когда оператор CRM нажмёт «Рассмотреть» ещё раз.
              </p>
            )}
            {latestDecision?.freeze && (
              <p className="verdict-action">
                Похож на человека из перечня Росфинмониторинга — проверьте, он ли это.
                <Link className="btn btn-primary btn-small" to="/compliance">
                  К проверке по перечням
                </Link>
              </p>
            )}
          </div>

          {/* «Принять» / «Отказать» — будущая отправка решения в CRM по API.
              Пока заглушки: ничего не отправляют, только объясняют. */}
          {canOperateFssp && (
            <div className="ah__actions decision-stub">
              <div className="decision-stub__row">
                <button
                  type="button"
                  className="btn decision-stub__btn decision-stub__btn--accept"
                  aria-pressed={decisionStub === "accept"}
                  aria-describedby="decision-stub-note"
                  onClick={() => setDecisionStub((v) => (v === "accept" ? null : "accept"))}
                >
                  Принять
                </button>
                <button
                  type="button"
                  className="btn decision-stub__btn decision-stub__btn--reject"
                  aria-pressed={decisionStub === "reject"}
                  aria-describedby="decision-stub-note"
                  onClick={() => setDecisionStub((v) => (v === "reject" ? null : "reject"))}
                >
                  Отказать
                </button>
                <span className="soon-tag">в разработке</span>
              </div>
              <p id="decision-stub-note" className="decision-stub__note">
                {decisionStub === null
                  ? "Решение по заявке уйдёт в CRM отсюда — скоро."
                  : `«${decisionStub === "accept" ? "Принять" : "Отказать"}» пока ничего не отправляет: передача решения в CRM по API в разработке.`}
              </p>
            </div>
          )}
        </div>

        <div className="ah__metrics">
          <div className="ah-metric">
            <div className="ah-metric__label">Балл БКИ</div>
            <div className="ah-metric__value">{score != null ? wholeNumber(score) : "—"}</div>
            {score != null &&
            latestDecision?.score_auto_approve_above != null &&
            latestDecision.score_auto_reject_below != null ? (
              <>
                <div
                  className="score-scale"
                  style={
                    {
                      "--reject-at": `${(Number(latestDecision.score_auto_reject_below) / 999) * 100}%`,
                      "--approve-at": `${(Number(latestDecision.score_auto_approve_above) / 999) * 100}%`,
                    } as React.CSSProperties
                  }
                >
                  <div className="marker" style={{ left: `${Math.min(100, Math.max(0, (Number(score) / 999) * 100))}%` }} />
                </div>
                <div className="ah-metric__hint">
                  отказ &lt; {wholeNumber(latestDecision.score_auto_reject_below)} · одобрение от{" "}
                  {wholeNumber(latestDecision.score_auto_approve_above)}
                </div>
              </>
            ) : (
              <div className="ah-metric__hint">{score != null ? "пороги не заданы" : "после стадии Б"}</div>
            )}
          </div>
          <div className="ah-metric">
            <div className="ah-metric__label">Долговая нагрузка</div>
            <div className="ah-metric__value">
              {ptiPercent != null ? `${ptiPercent}%` : "—"}
              {ptiPercent != null && ptiMaxPercent != null && <span className="ah-metric__of"> из {ptiMaxPercent}%</span>}
            </div>
            {ptiPercent != null && ptiMaxPercent != null ? (
              <div className="pti-bar">
                <i
                  className={ptiPercent > ptiMaxPercent ? "pti-bar__fill pti-bar__fill--bad" : "pti-bar__fill"}
                  style={{ width: `${Math.min(100, ptiPercent)}%` }}
                />
                <u className="pti-bar__limit" style={{ left: `${Math.min(100, ptiMaxPercent)}%` }} />
              </div>
            ) : (
              <div className="ah-metric__hint">после стадии Б</div>
            )}
          </div>
          <div className="ah-metric">
            <div className="ah-metric__label">Платёж в месяц</div>
            <div className="ah-metric__value">
              {latestDecision?.installment_payment ? `${formatAmount(latestDecision.installment_payment)} ₽` : "—"}
            </div>
            <div className="ah-metric__hint">
              {app.term_months > 0 ? `взнос ${formatAmount(app.down_payment)} ₽ · ${app.term_months} мес.` : "после «Рассмотреть»"}
            </div>
          </div>
          <div className="ah-metric">
            <div className="ah-metric__label">Рейтинг клиента</div>
            <div className={`ah-metric__value ${ratingTone}`}>
              {app.client_rating ? (app.client_rating.total > 0 ? `+${app.client_rating.total}` : String(app.client_rating.total)) : "—"}
            </div>
            <div className="ah-metric__hint">
              {app.client_rating ? (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => document.getElementById("client-rating")?.scrollIntoView({ behavior: "smooth" })}
                >
                  что повлияло ↓
                </button>
              ) : (
                "после «Рассмотреть»"
              )}
            </div>
          </div>
          <section className="ah-metric ah-metric--soon">
            <div className="ah-metric__head">
              <h3 className="ah-metric__label">Анализ ИИ</h3>
              <span className="soon-tag">в разработке</span>
            </div>
            <div className="ah-metric__value ah-metric__value--muted">—</div>
            <div className="ah-metric__hint">Анализ пока не проводится и на решение не влияет.</div>
          </section>
        </div>

        <ol className="journey" aria-label="Путь заявки">
          {journey.map((step, i) => (
            <li key={step.label} className={`journey__step journey__step--${step.state}`}>
              <span className="journey__dot" aria-hidden="true">
                {JOURNEY_ICON[step.state] ?? i + 1}
              </span>
              <span className="journey__label">{step.label}</span>
              <span className="journey__note">{step.note}</span>
            </li>
          ))}
        </ol>
      </section>

      {/* Кредитная история — отдельным блоком: запрос в БКИ платный, по кнопке. */}
      {(bkiWaiting || bkiRetry) && (
        <BkiRequestPanel
          applicationId={app.id}
          types={app.bki_report_types ?? []}
          order={app.bki_order}
          canOperate={canOperateFssp}
          retryReason={bkiRetry ? reasonCodeLabel(latestDecision?.reason_code ?? "") : null}
        />
      )}

      {showManual && (
        // Вид карточки не меняется, когда одна проверка пройдена: те же
        // разделы на тех же местах, меняется только подсветка.
        <div className={manualActionCount > 0 ? "pf-card pf-action" : "pf-card pf-action pf-action--done"}>
          {manualActionCount > 0 ? (
            <div className="pf-action__head">
              <span className="pf-action__icon" aria-hidden="true">
                !
              </span>
              Ручные проверки — нужно ваше действие
            </div>
          ) : (
            <h2 className="pf-section-title">Ручные проверки</h2>
          )}
          {leftText && <p className="pf-action__left">Осталось: {leftText}</p>}
          {fsspSection}
          {fedresursSection}
        </div>
      )}

      {app.client_rating && <ClientRatingPanel rating={app.client_rating} />}

      <section className="pf-card pf-feed">
        <h2 className="pf-section-title">Ход заявки</h2>
        <ol className="feed">
          {events.map((e) => (
            <FeedRow key={e.key} event={e} />
          ))}
        </ol>
        {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}
      </section>

      {/* Всё вспомогательное — ниже и свёрнуто: открывают, когда нужно. */}
      <div className="more">
        <h2 className="more__title">Данные заявки</h2>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Анкета клиента</h2>
            <span className="more__hint">паспорт, адрес, работа, доход{app.guarantor_profile ? ", поручитель" : ""}</span>
          </summary>
          <div className="more__body">
            {profileRows.length > 0 ? <KeyValue rows={profileRows} /> : <p className="muted">Анкеты пока нет.</p>}
            {app.guarantor_profile && (
              <>
                <h3 className="subhead">Поручитель</h3>
                <KeyValue
                  rows={Object.entries(app.guarantor_profile)
                    .filter(([, value]) => value != null && value !== "")
                    .map(([field, value]) => ({
                      label: humanizeFieldPath(field),
                      value: field.endsWith("_date") ? formatDate(String(value)) : String(value),
                    }))}
                />
              </>
            )}
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Условия сделки</h2>
            <span className="more__hint">
              {dealBlock.length > 0 ? "товар, взнос, срок и тариф, платёж" : "появятся после «Рассмотреть»"}
            </span>
          </summary>
          <div className="more__body">
            {dealBlock.length > 0 ? <KeyValue rows={dealBlock} /> : <p className="muted">Сделка ещё не оформлена в CRM.</p>}
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Что проверили</h2>
            <span className="more__hint">все проверки и повторы одной таблицей · {visibleChecks.length + bureauChecks.length}</span>
          </summary>
          <div className="more__body">
            <table className="table">
              <thead>
                <tr>
                  <th>Источник</th>
                  <th>Кто</th>
                  <th>Результат</th>
                  <th>Время</th>
                  <th>Ошибка</th>
                </tr>
              </thead>
              <tbody>
                {/* T17, шаг 3 (ваше явное указание): МВД не показываем — файл
                    недействительных паспортов отменён в 2023. */}
                {visibleChecks.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <SourceBadge source={c.source} />
                    </td>
                    <td className="muted">{subjectTypeLabel(c.subject_type)}</td>
                    <td>{checkResultLabel(c.source, c.status)}</td>
                    <td className="muted">{formatDateTime(c.started_at)}</td>
                    <td className="muted">{c.error_text ?? "—"}</td>
                  </tr>
                ))}
                {bureauChecks.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <SourceBadge source={b.source} /> <BkiTag />
                    </td>
                    <td className="muted">{subjectTypeLabel(b.subject_type)}</td>
                    <td>{checkResultLabel(b.source, b.status)}</td>
                    <td className="muted">{formatDateTime(b.created_at)}</td>
                    <td className="muted">—</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Документы</h2>
            <span className="more__hint">фото паспорта · {app.documents.length}</span>
          </summary>
          <div className="more__body">
            {app.documents.length === 0 && <p className="muted">Документов нет.</p>}
            <ul className="plain-list">
              {app.documents.map((doc) => (
                <li key={doc.id}>
                  <div className="row">
                    <span>{documentKindLabel(doc.kind)}</span>
                    <span className="muted">{formatDateTime(doc.uploaded_at)}</span>
                    <button className="btn btn-small" onClick={() => setOpenDocumentId(openDocumentId === doc.id ? null : doc.id)}>
                      {openDocumentId === doc.id ? "Скрыть" : "Показать"}
                    </button>
                  </div>
                  {openDocumentId === doc.id && (
                    <img
                      className="document-preview"
                      src={`/api/applications/${app.id}/documents/${doc.id}`}
                      alt={documentKindLabel(doc.kind)}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">История</h2>
            <span className="more__hint">смена статусов · {app.status_history.length}</span>
          </summary>
          <div className="more__body">
            <table className="table">
              <thead>
                <tr>
                  <th>Из</th>
                  <th>В</th>
                  <th>Кто</th>
                  <th>Когда</th>
                </tr>
              </thead>
              <tbody>
                {app.status_history.map((h, i) => (
                  <tr key={i}>
                    <td>
                      <Badge value={h.from_status} label={h.from_status ? applicationStatusLabel(h.from_status) : undefined} />
                    </td>
                    <td>
                      <Badge value={h.to_status} label={applicationStatusLabel(h.to_status)} />
                    </td>
                    <td className="muted">{actorTypeLabel(h.actor_type)}</td>
                    <td className="muted">{formatDateTime(h.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        {/* Сырьё (T17, часть 2): паспортные поля и полные снимки анкеты. */}
        <details className="more__item disclosure">
          <summary>
            <span className="more__name">Подробности</span>
            <span className="more__hint">паспортные данные и анкеты из CRM как есть</span>
          </summary>
          <div className="more__body">
            {app.ocr_result ? (
              <>
                <h3 className="subhead">
                  Паспортные данные · мин. уверенность {app.ocr_result.min_confidence ?? "—"} ·{" "}
                  {formatDateTime(app.ocr_result.created_at)}
                </h3>
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>Поле</th>
                      <th>Значение</th>
                      <th>Уверенность</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries((app.ocr_result.fields.fields as Record<string, unknown>) ?? {}).map(([field, value]) => {
                      const v = value as { value?: unknown; confidence?: number };
                      return (
                        <tr key={field}>
                          <td>{humanizeFieldPath(field)}</td>
                          <td>{String(v.value ?? "—")}</td>
                          <td className="muted">{v.confidence ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            ) : null}
            <h3 className="subhead">Снимки анкеты</h3>
            <SnapshotHistory snapshots={app.external_snapshots} />
          </div>
        </details>
      </div>
    </div>
  );
}

function DecisionReasons({ explanation }: { explanation: NonNullable<ReturnType<typeof explainDecision>> }) {
  if (explanation.causes.length === 0 && explanation.passed.length === 0 && explanation.unknown.length === 0) {
    return null;
  }
  return (
    <div className="stage__reasons">
      {explanation.causes.length > 0 && (
        <>
          <h3 className="subhead">Причина</h3>
          <ul className="reason-list" data-testid="decision-causes">
            {explanation.causes.map((text) => (
              <li key={text} className="reason-item reason-item--cause">{text}</li>
            ))}
          </ul>
        </>
      )}
      {explanation.passed.length > 0 && (
        <>
          <h3 className="subhead">В норме</h3>
          <ul className="reason-list reason-list--minor" data-testid="decision-passed">
            {explanation.passed.map((text) => (
              <li key={text} className="reason-item reason-item--pass">{text}</li>
            ))}
          </ul>
        </>
      )}
      {explanation.unknown.length > 0 && (
        <ul className="reason-list reason-list--minor" data-testid="decision-unknown">
          {explanation.unknown.map((text) => (
            <li key={text} className="reason-item">{text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function checkTone(status: string): "ok" | "bad" | "warn" {
  if (status === "hit") return "bad";
  if (status === "source_unavailable" || status === "error") return "warn";
  return "ok";
}

type PassportCheck = components["schemas"]["PassportCheckOut"];

const PASSPORT_TONE: Record<string, "ok" | "bad" | "warn"> = { ok: "ok", fail: "bad", warn: "warn", unknown: "warn" };
const TONE_ICON = { ok: "✓", bad: "✕", warn: "!" } as const;

// T23: паспорт виден в карточке всегда, а не только при отказе. Одна строка
// с итогом; по кнопке — что именно проверено и результат каждого пункта.
function PassportRow({ items }: { items: PassportCheck[] }) {
  const [open, setOpen] = useState(false);
  const tone = items.some((i) => i.status === "fail")
    ? "bad"
    : items.some((i) => i.status !== "ok")
      ? "warn"
      : "ok";
  const problems = items.filter((i) => i.status !== "ok").length;
  const summary =
    tone === "ok"
      ? `в порядке · ${items.length} из ${items.length}`
      : tone === "bad"
        ? "нарушение"
        : `замечаний: ${problems}`;
  return (
    <li className={`checklist__item checklist__item--${tone} passport-row`}>
      <span className="checklist__icon" aria-hidden="true">
        {TONE_ICON[tone]}
      </span>
      <span className="checklist__source">Паспорт</span>
      <span className="checklist__result">{summary}</span>
      <button
        type="button"
        className="btn-link passport-row__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "скрыть" : "что проверили"}
      </button>
      {open && (
        <ul className="passport-row__items">
          {items.map((item) => {
            const itemTone = PASSPORT_TONE[item.status] ?? "warn";
            return (
              <li key={item.key} className={`checklist__item checklist__item--${itemTone}`}>
                <span className="checklist__icon" aria-hidden="true">
                  {TONE_ICON[itemTone]}
                </span>
                <span>{passportCheckLabel(item.key)}</span>
                <span className="checklist__result">{passportCheckStatusLabel(item.status)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

function CheckList({
  checks,
  canRetry,
  retry,
  busy,
  leading = null,
}: {
  checks: CheckRow[];
  canRetry: boolean;
  retry: (checkId: string) => void;
  busy: boolean;
  /** Строка перед проверками источников (T23: паспорт стадии А). */
  leading?: React.ReactNode;
}) {
  if (checks.length === 0 && !leading) return <p className="stage__reason muted">Проверок пока нет.</p>;
  return (
    <ul className="checklist">
      {leading}
      {checks.map((c) => (
        <li key={c.id} className={`checklist__item checklist__item--${checkTone(c.status)}`}>
          <span className="checklist__icon" aria-hidden="true">
            {checkTone(c.status) === "ok" ? "✓" : checkTone(c.status) === "bad" ? "✕" : "!"}
          </span>
          <span className="checklist__source">
            <SourceBadge source={c.source} />
            {c.subject_type === "guarantor" && <span className="muted"> · поручитель</span>}
          </span>
          <span className="checklist__result">{checkResultLabel(c.source, c.status)}</span>
          {c.can_retry && canRetry && (
            <button className="btn btn-small" disabled={busy} onClick={() => retry(c.id)}>
              Повторить
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function KeyValue({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <dl className="kv-grid">
      {rows.map((row) => (
        <div key={row.label} className="kv-row">
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
